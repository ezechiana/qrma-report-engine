"""Arbonne catalogue family classification; product-purpose taxonomy, not medical efficacy."""
import re

def normalise(value):
    return re.sub(r'\s+', ' ', str(value or '').strip().lower())

BRANDS = {'bewell':'BeWell', 'guthealth':'GutHealth', 'dermresults':'DermResults', 'skinelixir':'SkinElixir', 'energyfizz':'EnergyFizz', 'gre​ensynergy':'GreenSynergy', 'greensynergy':'GreenSynergy', 'cleantox':'CleanTox'}
BEAUTY = ('dermresults','skinelixir','skin care','skincare','serum','facial','face cream','face mask','eye mask','eye cream','lip balm','lip gloss','lipstick','mascara','foundation makeup','makeup','concealer','toner','exfoliat','beauty','glow cream','glow serum','moisturizing lip')
PERSONAL = ('bodycare','body care','body cleanser','body moisturizer','shampoo','conditioner','hair care','haircare','deodorant','hand wash','body wash','body lotion','bath','body scrub')
NUTRITION = ('guthealth','bewell','daily core','essentialmeal','energyfizz','greensynergy','green synergy','vitamin','mineral','fibre','fiber','protein','nutrition','greens','superfood','digestion','microbiome','probiotic','supplement','herbal infusion','cleanse','ginseng','collagen builder')

def classify(name):
    t = normalise(name)
    # Priority: a topical skin product mentioning vitamin C is not a vitamin supplement.
    if any(k in t for k in BEAUTY):
        group='beauty_skincare'; label='Beauty & Skincare'
    elif any(k in t for k in PERSONAL):
        group='body_personal_care'; label='Body & Personal Care'
    elif any(k in t for k in NUTRITION):
        group='nutrition_wellness'; label='Nutrition & Wellness'
    else:
        group='unclassified'; label='Unclassified — Review Required'
    return {'product_category':group,'product_category_label':label,'category_confidence':'PROVISIONAL_NAME_RULE','category_review_required':True}

def display_name(name):
    # Catalogue currently stores lowercase names. Do not pretend to recover
    # manufacturer-approved spelling from title case; restore known brand tokens only.
    raw=str(name or '').strip()
    if not raw: return raw
    result=raw[0].upper()+raw[1:] if raw==raw.lower() else raw
    for token,official in BRANDS.items():
        result=re.sub(re.escape(token),official,result,flags=re.I)
    return result
