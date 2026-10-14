"""Opt-in experimental directional shadow scoring; no public report changes."""
import os,logging,hashlib,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/'app/data/go360_directional_shadow'
ENGINE=ROOT/'app/services/pattern_engine_v2.py'
ENGINE_SHA='e006ded4a2e25debc6e3d69cc114a26f6b0476d8e69496fbeb5097b3f915ae3c'
LOGGER=logging.getLogger('go360.directional_shadow')
# B.3.11.2C: isolate diagnostic INFO output to Docker stdout.
# Do not change the application's root logger or report content.
LOGGER.setLevel(logging.INFO)
if not any(getattr(h, '_go360_shadow_diagnostic', False) for h in LOGGER.handlers):
 _go360_handler=logging.StreamHandler(sys.stdout)
 _go360_handler.setLevel(logging.INFO)
 _go360_handler.setFormatter(logging.Formatter('%(levelname)s %(name)s %(message)s'))
 _go360_handler._go360_shadow_diagnostic=True
 LOGGER.addHandler(_go360_handler)
LOGGER.propagate=False
def run_shadow(report):
 if os.getenv('GO360_DIRECTIONAL_PATTERN_SHADOW','0')!='1':return None
 if not ENGINE.is_file() or hashlib.sha256(ENGINE.read_bytes()).hexdigest()!=ENGINE_SHA:
  LOGGER.warning('GO360_SHADOW_SKIPPED_ENGINE_GUARD');return None
 if not DATA.is_dir():LOGGER.warning('GO360_SHADOW_SKIPPED_MISSING_DATA');return None
 if str(DATA) not in sys.path:sys.path.insert(0,str(DATA))
 try:
  from canonical_calibration import compute
  from application_parallel import read_rules
  from app.services import pattern_engine_v2 as engine
  observations,evidence,patterns,legacy=compute(report,engine,read_rules())
  # Aggregate diagnostic only: never log patient, raw markers or measurement values.
  result={'stage':'B.3.10.R1','mode':'SHADOW_ONLY',
   'observations':len(observations),
   'resolved':sum(bool(o['canonical_id']) for o in observations),
   'patterns':[{'key':p['pattern'],'candidate_score':p['calibrated_candidate_score'],
    'candidate_rank':p['candidate_rank'],'qualified_markers':p['qualified_unique_markers']} for p in patterns]}
  LOGGER.info('GO360_DIRECTIONAL_SHADOW_SUMMARY %s',result)
  return result
 except Exception:
  LOGGER.exception('GO360_DIRECTIONAL_SHADOW_FAILED')
  return None
 finally:
  # No mutation of report or production pattern fields.
  if sys.path and sys.path[0]==str(DATA):sys.path.pop(0)
