# app/data/vitalhealth_map.py

from typing import Dict, List

VITALHEALTH_CATEGORY_MAP: Dict[str, Dict[str, List[str]]] = {
    "Cardiovascular and Cerebrovascular": {
        "products": ["V-OMEGA 3", "V-ASCULAX", "V-NITRO"],
    },
    "Gastrointestinal Function": {
        "products": ["V-FORTIFLORA", "V-CONTROL", "V-TEDETOX"],
    },
    "Large Intestine Function": {
        "products": ["V-TEDETOX", "V-CONTROL", "V-NEUROKAFE", "V-FORTIFLORA"],
    },
    "Liver Function": {
        "products": ["V-ORGANEX", "V-TEDETOX", "V-CURCUMAX", "V-GLUTATION PLUS"],
    },
    "Gallbladder Function": {
        "products": ["V-ORGANEX", "V-CURCUMAX"],
    },
    "Pancreatic Function": {
        "products": ["V-ORGANEX", "V-GLUTATION", "S-BALANCE"],
    },
    "Kidney Function": {
        "products": ["V-ITAREN", "V-ASCULAX"],
    },
    "Lung Function": {
        "products": ["V-CURCUMAX", "VITALBOOST"],
    },
    "Brain Nerve": {
        "products": ["V-NEUROKAFE", "V-ITALAY", "V-OMEGA 3"],
    },
    "Bone Disease": {
        "products": ["V-ITADOL", "VITALAGE COLLAGEN", "V-CURCUMAX"],
    },
    "Bone Mineral Density": {
        "products": ["VITALAGE COLLAGEN", "V-DAILY"],
    },
    "Rheumatoid Bone Disease": {
        "products": ["V-ITADOL", "VITALAGE COLLAGEN", "V-CURCUMAX", "V-OMEGA 3"],
    },
    "Bone Growth Index": {
        "products": ["VITALPRO", "V-DAILY", "V-OMEGA 3"],
    },
    "Blood Sugar": {
        "products": ["S-BALANCE","V-OMEGA 3", "V-GLUTATION"],
    },
    "Trace Element": {
        "products": ["VITALPRO", "V-DAILY", "V-OMEGA 3"],
    },
    "Vitamin": {
        "products": ["V-NRGY", "VITALPRO", "V-DAILY"],
    },
    "Amino Acid": {
        "products": ["V-NRGY", "VITALPRO", "V-DAILY"],
    },     
    "Coenzyme": {
        "products": ["V-NRGY", "VITALPRO", "V-DAILY"],
    },  
    "Essential Fatty Acid": {
        "products": ["V-OMEGA 3", "VITALPRO"],
    },   
    "Endocrine System": {
        "products": ["V-DAILY"],
    },   
    "Immune System": {
        "products": ["VITALBOOST", "V-DAILY", "V-GLUTATION PLUS"],
    },
    "Thyroid": {
        "products": ["V-ITALAY", "V-LOVKAFE", "V-OMEGA 3", "V-CURCUMAX", "V-GLUTATION PLUS"],
    }, 
  
    "Human Toxin": {
        "products": ["V-DAILY", "V-GLUTATION PLUS", "V-TEDETOX"],
    },
    "Heavy Metal": {
        "products": ["V-GLUTATION", "V-GLUTATION PLUS", "V-TEDETOX"],
    },
    "Basic Physical Quality": {
        "products": ["V-GLUTATION", "V-ORGANEX", "V-ITAREN", "V-DAILY"],
    },   
    "Allergy": {
        "products": ["VITALBOOST", "V-ORGANEX", "V-CURCUMAX"],
    },
    "Obesity": {
        "products": ["V-TEDETOX", "V-CONTROL", "VITALPRO", "V-THERMOKAFE"],
    },
    "Skin": {
        "products": ["VITALAGE COLLAGEN", "V-GLUTATION", "V-GLUTATION PLUS", "V-DAILY"],
    },
    "Eye": {
        "products": ["V-OMEGA 3", "V-NITRO", "V-DAILY"],
    },
    "Collagen": {
         "products": ["VITALAGE COLLAGEN"],
    },
    "Channels and collaterals": {
         "products": ["VITALAY"],
    },
    "Pulse of heart and brain": {
         "products": ["V-OMEGA 3", "V-ITALAY", "V-NEUROKAFE", "V-ASCULUX"],
    },
    "Blood Lipids": {
         "products": ["V-OMEGA 3", "V-NITRO", "V-ASCULUX"],
    },
    "Prostate": {
         "products": ["V-GLUTATION","V-OMEGA 3", "V-CURCUMAX", "V-LOVKAFE", "V-ITADOL"],
    },
    "Male Sexual Function": {
         "products": ["V-NITRO","V-NRGY", "V-LOVKAFE"],
    },
    "Sperm and sement": {
         "products": ["V-NITRO","V-DAILY", "V-GLUTATION", "V-LOVKAFE"],
    },
     "Male Hormone": {
         "products": ["V-NITRO","V-DAILY", "V-GLUTATION", "V-LOVKAFE"],
    },
     "Gynecology": {
         "products": ["V-DAILY", "S-BALANCE", "V-LOVKAFE", "V-OMEGA 3"],
    },
    "Breast": {
         "products": ["V-GLUTATION PLUS", "V-LOVKAFE", "V-OMEGA 3"],
    },
    "Menstrual cycle": {
         "products": ["S-BALANCE", "V-DAILY", "VITALAGE COLLAGEN"],
    },
    "Menstrual cycle": {
         "products": ["S-BALANCE", "V-DAILY", "VITALAGE COLLAGEN"],
    },
    "ADHD": {
         "products": ["V-DAILY", "V-ITALAY", "V-OMEGA 3", "V-GLUTATION PLUS", "V-NEUROKAFE", "VITALPRO"],
    },
}