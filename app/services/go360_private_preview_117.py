"""Local-only, secret-gated, no patient data; zero approval/publication."""
import os,json,pathlib,hmac,ipaddress
from fastapi import APIRouter,Request,HTTPException
from fastapi.responses import JSONResponse
router=APIRouter(prefix='/__go360_local_arbonne_preview_117',include_in_schema=False)
ROOT=pathlib.Path(__file__).resolve().parents[2]/'GO360_ARB_2F2D11_7'
def guard(request):
    if os.environ.get('GO360_ENV','').lower() not in ('local','development','test') or os.environ.get('GO360_ARB_PRIVATE_PREVIEW_117')!='1':
        raise HTTPException(404)
    token=os.environ.get('GO360_ARB_PREVIEW_TOKEN','')
    if len(token)<24 or not hmac.compare_digest(request.headers.get('x-go360-preview-token',''),token):
        raise HTTPException(404)
    # Request must arrive from loopback; no forwarded header trust.
    host=request.client.host if request.client else ''
    try:
        if not (ipaddress.ip_address(host).is_loopback):raise HTTPException(404)
    except ValueError:
        if not (host == 'testclient' and os.environ.get('GO360_ENV') == 'test'):
            raise HTTPException(404)
@router.get('/review',include_in_schema=False)
def review(request:Request,market:str='gb',language:str='en'):
    guard(request)
    if (market,language) not in {('gb','en'),('us','en'),('us-es','es'),('ca','en'),('ca-fr','fr'),('pl','pl'),('au','en'),('nz','en')}:
        raise HTTPException(400,'Unsupported edition')
    registry=json.loads((ROOT/'input_family_registry.json').read_text(encoding='utf-8'))
    bundles=json.loads((ROOT/'input_bundle_registry.json').read_text(encoding='utf-8'))
    products=[]
    for p in registry['parents']:
        variants=[{'sku':o['sku'],'name':o['storefront_product_name'],'variant':o.get('variant'),'status':'UK_STOREFRONT_OBSERVED','approved':False} for o in p.get('uk_storefront_observations',[]) if o['market']==market and o['language']==language]
        products.append({'parent_id':p['parent_id'],'product':p['parent_name'],'family_candidate':p.get('family_label_candidate'),'family_approved':False,'variants':variants,'bundle_options':[],'publication_eligible':False})
    response={'stage':'2F.2D.11.7','mode':'PRIVATE_LOCAL_ONLY','market':market,'language':language,'products':products,'bundle_candidates':len(bundles['bundles']),'verified_bundle_memberships':0,'client_report_eligible':0,'no_patient_data':True}
    return JSONResponse(response,headers={'Cache-Control':'no-store','X-Robots-Tag':'noindex, nofollow'})
