"""Queue/retry failure tests; fake reversible protection only in unit tests."""
import importlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parents[1]/'desktop'))
from go360_connector.core import Queue,stable_capture,validate_folder
from go360_connector.transfer import transfer,TransferError,Client
import pytest

class Protection:
    def encrypt(self,data):return b'protected:'+bytes(b^0x73 for b in data)
    def decrypt(self,data):return bytes(b^0x73 for b in data[10:])

@pytest.fixture
def queue(tmp_path):return Queue(tmp_path/'queue.db',Protection(),clock=lambda:1000000)

RAW=b'<html>assessment secret</html>'


def test_restart_overwrite_and_account_binding(queue):
    a=queue.capture('https://dev','a','source',RAW)
    assert queue.capture('https://dev','a','source',RAW) is None
    b=queue.capture('https://dev','a','source',RAW+b'changed')
    c=queue.capture('https://dev','a','source',RAW)
    assert len({a,b,c})==3
    assert queue.jobs('https://dev','b')==[]
    queue.patch(queue.jobs('https://dev','a')[0],state='uploading')
    recovered=Queue(queue.path,Protection(),clock=queue.clock)
    assert recovered.jobs('https://dev','a')[0]['state']=='failed_retryable'
    assert recovered.protection.decrypt(recovered.jobs('https://dev','a')[0]['payload'])==RAW
    assert b'assessment secret' not in queue.path.read_bytes()


def test_retention_pending_unresolved_protected(queue):
    queue.capture('https://dev','a','one',RAW)
    queue.capture('https://dev','a','two',RAW+b'2')
    queue.capture('https://dev','a','three',RAW+b'3')
    a,b,c=queue.jobs('https://dev','a')
    queue.patch(a,state='awaiting_practitioner',server={'status':'awaiting_practitioner'})
    queue.patch(b,state='needs_attention',server={'status':'needs_attention'})
    queue.clock=lambda:1000000+7*86400-1
    assert queue.cleanup()==0
    queue.clock=lambda:1000000+7*86400
    assert queue.cleanup()==1
    rows=queue.jobs('https://dev','a')
    assert rows[0]['payload'] is None
    assert rows[1]['payload'] and rows[2]['payload']


def test_timeout_reconciles_original_job(queue):
    job=queue.capture('https://dev','a','source',RAW)
    class Server:
        origin='https://dev'
        saved=False
        calls=[]
        def request(self,method,path,**kwargs):
            self.calls.append(method)
            if method=='GET' and not self.saved:raise TransferError(404)
            if method=='POST':self.saved=True;raise TransferError()
            return {'job_id':job,'connector_id':'connector','status':'awaiting_practitioner','assessment_id':'assessment'}
    server=Server();session={'account_id':'a','connector_id':'connector','access_token':'secret'}
    transfer(queue,server,session)
    assert queue.jobs('https://dev','a')[0]['state']=='failed_retryable'
    queue.clock=lambda:1000010
    transfer(queue,server,session)
    assert server.calls==['GET','POST','GET']
    assert queue.jobs('https://dev','a')[0]['state']=='awaiting_practitioner'
    transfer(queue,server,{**session,'account_id':'b'})
    assert server.calls==['GET','POST','GET']


def test_partial_write_and_capture_race(tmp_path):
    source=tmp_path/'ResultOne.htm';source.write_bytes(RAW)
    reads=iter([RAW,RAW+b'change'])
    with pytest.raises(OSError):stable_capture(source,lambda path:next(reads),wait=lambda _:None)
    assert stable_capture(source,lambda path:RAW,wait=lambda _:None)==RAW
    with pytest.raises(ValueError):validate_folder(tmp_path)
    (tmp_path/'Result01.htm').write_bytes(RAW);(tmp_path/'ReportE').mkdir()
    assert validate_folder(tmp_path)==source


def test_permanent_auth_and_retry(queue):
    queue.capture('https://dev','a','source',RAW)
    class Server:
        origin='https://dev'
        status=422
        def request(self,*args,**kwargs):raise TransferError(self.status)
    server=Server();session={'account_id':'a','connector_id':'connector','access_token':'secret'}
    transfer(queue,server,session)
    assert queue.jobs('https://dev','a')[0]['state']=='needs_attention'
    queue.retry(server.origin,'a');server.status=401
    with pytest.raises(TransferError):transfer(queue,server,session)
    assert queue.jobs('https://dev','a')[0]['payload'] is not None
    with pytest.raises(ValueError):Client('http://localhost:8000')
    with pytest.raises(ValueError):Client('https://user:password@localhost')

def test_capture_transaction_failure_preserves_retryability(tmp_path):
    class FailingProtection(Protection):
        calls=0
        def encrypt(self,data):
            self.calls+=1
            if self.calls==2:raise OSError('synthetic disk/protection failure')
            return super().encrypt(data)
    q=Queue(tmp_path/'queue.db',FailingProtection())
    with pytest.raises(OSError):q.capture('https://dev','a','source',RAW)
    assert q.jobs('https://dev','a')==[]
    q.protection=Protection()
    assert q.capture('https://dev','a','source',RAW)


def test_retry_backoff_is_bounded_and_does_not_discard(queue):
    queue.capture('https://dev','a','source',RAW)
    row=queue.jobs('https://dev','a')[0]
    row['attempts']=10000
    queue.patch(row,state='failed_retryable',error='temporarily_unavailable',retry=True)
    stored=queue.jobs('https://dev','a')[0]
    assert 0<stored['next_attempt']-queue.clock()<=3600
    assert stored['attempts']==10001 and stored['payload'] is not None
