import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)
 
# ---------------------------------------------------------------------------
# Master registry
# ---------------------------------------------------------------------------
 
ENUM_REGISTRY: dict[str, dict[str, dict[str, Any]]] = {
 
    # ── BNR_Service_User ────────────────────────────────────────────────────
   "BNR_Service_User": {
        "Status": {
            "db_type": "int",
            "synonyms": {
                "active": 1,
                "enabled": 1,
                "maternityleave": 2,
                "maternity_leave": 2,
                "maternity leave": 2,
                "longtermleave": 3,
                "long_term_leave": 3,
                "long term leave": 3,
                "suspension": 4,
                "suspended": 4,
                "inactive_status": 5,
                "inactive_user": 5,
                "deactive": 5,
                "deactivated": 5,
                "inactive": 5,
                "disabled": 5,
            }
        }
    },
 
    # ── BNR_Sites ────────────────────────────────────────────────────────────
    "BNR_Sites": {
        "Status": {
            "db_type": "tinyint",
            "synonyms": {
                "deactive": 0,
                "deactivated": 0,
                "inactive": 0,
                "disabled": 0,
                "active": 1,
                "enabled": 1,
            },
        },
    },
 
    # ── BNR_UserDetails (Staff) ──────────────────────────────────────────────
    "BNR_UserDetails": {
        "UserType": {
            "db_type": "int",
            "synonyms": {
                "super admin": 2,
                "superadmin": 2,
                "site admin": 3,
                "siteadmin": 3,
                "manager": 4,
                "operator": 5,
                "staff": 5,
                "service user": 6,
                "client": 6,
                "visitor": 7,
                "guest": 7,
            },
        },
        "Status": {
            "db_type": "int",
            "synonyms": {
                "active": 1,
                "enabled": 1,
                "maternityleave": 2,
                "maternity_leave": 2,
                "maternity leave": 2,
                "longtermleave": 3,
                "long_term_leave": 3,
                "long term leave": 3,
                "suspension": 4,
                "suspended": 4,
                "inactive_status": 5,
                "inactive_user": 5,
                "inactive account": 5,
                "deactive": 5,
                "deactivated": 5,
                "inactive": 5,
                "disabled": 5,
            },
        },
    },
 
    # ── BNR_TicketMaster ─────────────────────────────────────────────────────
    "BNR_TicketMaster": {
        "UserStatus": {
            "db_type": "int",
            "synonyms": {
                "submitted": 1,
                "open": 1,
                "pending": 2,
                "in progress": 2,
                "closed": 3,
                "resolved": 3,
                "done": 3,
            },
        },
        "AdminStatus": {
            "db_type": "int",
            "synonyms": {
                "submitted": 1,
                "open": 1,
                "pending": 2,
                "in progress": 2,
                "closed": 3,
                "resolved": 3,
                "done": 3,
            },
        },
    },
 
    # ── BNR_Daily_Logs ───────────────────────────────────────────────────────
    "BNR_Daily_Logs": {
        "Reason": {
            "db_type": "int",
            "synonyms": {
                "family contact": 1,
                "family": 1,
                "professional visit": 2,
                "professional": 2,
                "health appointment": 3,
                "health": 3,
                "appointment": 3,
                "general notes": 4,
                "general": 4,
                "wow moment": 5,
                "wow": 5,
                "community access": 6,
                "community": 6,
                "support plan": 7,
                "routine": 8,
                "restrictive practice": 9,
                "restrictive": 9,
                "seizure log": 10,
                "seizure": 10,
                "finance record form": 11,
                "finance": 11,
                "food and fluid chart": 12,
                "food and fluid": 12,
                "food": 12,
                "fluid": 12,
                "communication passport": 13,
                "communication": 13,
            },
        },
        "RestrictivePracticeType": {
            "db_type": "int",
            "synonyms": {
                "prn": 1,
                "choice": 2,
                "access": 3,
                "dignity": 4,
                "privacy": 5,
                "other": 6,
                "assertive command": 7,
                "assertive": 7,
            },
        },
        "RestrictivePracticeAction": {
            "db_type": "int",
            "synonyms": {
                "promoting": 1,
                "promote": 1,
                "restricting": 2,
                "restrict": 2,
                "limiting": 3,
                "limit": 3,
            },
        },
        "Shift": {
            "db_type": "int",
            "synonyms": {
                "none": 0,
                "day": 1,
                "day shift": 1,
                "night": 2,
                "night shift": 2,
            },
        },
        "CommunicationPassportWhatISaidOrDid": {
            "db_type": "int",
            "synonyms": {
                "what i said": 1,
                "said": 1,
                "what i did": 2,
                "did": 2,
            },
        },
    },
 
    # ── BNR_Handover_Master ──────────────────────────────────────────────────
    "BNR_Handover_Master": {
        "Shift": {
            "db_type": "int",
            "synonyms": {
                "none": 0,
                "day": 1,
                "day shift": 1,
                "night": 2,
                "night shift": 2,
            },
        },
    },
 
    # ── BNR_ActivityPlanner ──────────────────────────────────────────────────
    "BNR_ActivityPlanner": {
        "ActivityType": {
            "db_type": "int",
            "synonyms": {
                "morning": 1,
                "afternoon": 2,
                "evening": 3,
            },
        },
        "Day": {
            "db_type": "int",
            "synonyms": {
                "monday": 1,
                "tuesday": 2,
                "wednesday": 3,
                "thursday": 4,
                "friday": 5,
                "saturday": 6,
                "sunday": 0,
            }
        }
    },
 
    # ── BNR_ActivityPlanner_Entry ────────────────────────────────────────────
    "BNR_ActivityPlanner_Entry": {
        "ActivityType": {
            "db_type": "int",
            "synonyms": {
                "morning": 1,
                "afternoon": 2,
                "evening": 3,
            },
        },
    },
 
    # ── BNR_Incidents ────────────────────────────────────────────────────────
    "BNR_Incidents": {
        "PersonAffected": {
            "db_type": "int",
            "synonyms": {
                "service user": 1,
                "client": 1,
                "staff": 2,
                "employee": 2,
                "visitor": 3,
                "guest": 3,
                "contractor": 4,
                "other": 5,
            },
        },
        "PerpetratorType": {
            "db_type": "int",
            "synonyms": {
                "service user": 1,
                "client": 1,
                "staff": 2,
                "employee": 2,
                "visitor": 3,
                "guest": 3,
                "contractor": 4,
                "other": 5,
            },
        },
        "AbuseType": {
            "db_type": "int",
            "synonyms": {
                "neglect": 1,
                "neglect or acts of omission": 1,
                "acts of omission": 1,

                "physical abuse": 2,
                "physical": 2,

                "financial abuse": 3,
                "financial or material abuse": 3,
                "material abuse": 3,

                "psychological abuse": 4,
                "emotional abuse": 4,
                "psychological or emotional abuse": 4,

                "sexual abuse": 5,
                "sexual": 5,

                "organisational abuse": 6,
                "institutional abuse": 6,
                "organisational or institutional abuse": 6,

                "discriminatory abuse": 7,
                "discrimination": 7,

                "self neglect": 8,
                "selfneglect": 8,

                "domestic violence": 9,
                "domestic abuse": 9,
                "domestic violence or abuse": 9,

                "modern slavery": 10,
                "slavery": 10,

                "other": 11,
                "others": 11
            }
        },
    },
 
    # ── BNR_IncidentWitness ──────────────────────────────────────────────────
    "BNR_IncidentWitness": {
        "WitnessType": {
            "db_type": "int",
            "synonyms": {
                "self": 1,
                "service user": 2,
                "client": 2,
                "staff": 3,
                "employee": 3,
                "other": 4,
            },
        },
        "Witness": {
            "db_type": "int",
            "synonyms": {
                "service user": 1,
                "serviceuser": 1,
                "client": 1,

                "staff": 2,
                "employee": 2,
                "staff member": 2,

                "family": 3,
                "family member": 3,
                "relative": 3,

                "professional": 4,
                "health professional": 4,
                "care professional": 4,

                "social worker": 5,
                "socialworker": 5,

                "commissioner": 6,

                "public": 7,
                "general public": 7,

                "other stakeholder": 8,
                "otherstakeholder": 8,
                "stakeholder": 8,
                "other": 8
            }
        }
    },
 
    # ── BNR_Safeguarding ─────────────────────────────────────────────────────
    "BNR_Safeguarding": {
        "PersonReporting": {
            "db_type": "int",
            "synonyms": {
                "service user": 1,
                "client": 1,
                "staff": 2,
                "employee": 2,
                "family": 3,
                "professional": 4,
                "social worker": 5,
                "commissioner": 6,
                "public": 7,
                "other stakeholder": 8,
                "other": 8,
            },
        },
        "WhoRaisedSafeguarding": {
            "db_type": "int",
            "synonyms": {
                "organization": 1,
                "organisation": 1,
                "internal": 1,
                "external": 2,
            },
        },
        "TypeOfSafeGuarding": {
            "db_type": "int",
            "synonyms": {
                "neglect": 1,
                "acts of omission": 1,
                "neglect or acts of omission": 1,
                "physical abuse": 2,
                "physical": 2,
                "financial abuse": 3,
                "material abuse": 3,
                "financial or material abuse": 3,
                "financial": 3,
                "psychological abuse": 4,
                "emotional abuse": 4,
                "psychological or emotional abuse": 4,
                "psychological": 4,
                "emotional": 4,
                "sexual abuse": 5,
                "sexual": 5,
                "organisational abuse": 6,
                "institutional abuse": 6,
                "organisational or institutional abuse": 6,
                "discriminatory abuse": 7,
                "discriminatory": 7,
                "self neglect": 8,
                "selfneglect": 8,
                "domestic violence": 9,
                "domestic abuse": 9,
                "modern slavery": 10,
                "slavery": 10,
                "other": 11,
            },
        },
        "PersonAtRisk": {
            "db_type": "int",
            "synonyms": {
                "service user": 1,
                "client": 1,
                "staff": 2,
                "employee": 2,
                "visitor": 3,
                "contractor": 4,
                "other": 5,
            },
        },
        "PersonCausingType": {
            "db_type": "int",
            "synonyms": {
                "service user": 1,
                "client": 1,
                "staff": 2,
                "employee": 2,
                "visitor": 3,
                "contractor": 4,
                "other": 5,
            },
        },
    },
 
    # ── BNR_SafeguardingWitness ──────────────────────────────────────────────
    "BNR_SafeguardingWitness": {
        "Witness": {
            "db_type": "int",
            "synonyms": {
                "service user": 1,
                "serviceuser": 1,
                "client": 1,

                "staff": 2,
                "employee": 2,

                "family": 3,
                "family member": 3,
                "relative": 3,

                "professional": 4,
                "health professional": 4,
                "care professional": 4,

                "social worker": 5,
                "socialworker": 5,

                "commissioner": 6,

                "public": 7,
                "general public": 7,

                "other stakeholder": 8,
                "otherstakeholder": 8,
                "stakeholder": 8,
                "other": 8
            }
        }
    },
 
    # ── BNR_SafeguardingPerpetrator ──────────────────────────────────────────
    "BNR_SafeguardingPerpetrator": {
        "Perpetrator": {
            "db_type": "int",
            "synonyms": {
                "service user": 1,
                "serviceuser": 1,
                "client": 1,

                "staff": 2,
                "employee": 2,

                "family": 3,
                "family member": 3,
                "relative": 3,

                "professional": 4,
                "health professional": 4,
                "care professional": 4,

                "social worker": 5,
                "socialworker": 5,

                "commissioner": 6,

                "public": 7,
                "general public": 7,

                "other stakeholder": 8,
                "otherstakeholder": 8,
                "stakeholder": 8,
                "other": 8
            }
        },
    },
 
    # ── BNR_ComplaintOrConcernOrComplement ───────────────────────────────────
    "BNR_ComplaintOrConcernOrComplement": {
        "ComplaintOrConcernOrComplementType": {
            "db_type": "int",
            "synonyms": {
                "complaint": 1,
                "concern": 2,
                "compliment": 3,
                "complement": 3,
            },
        },
        "ComplainantUserType": {
            "db_type": "int",
            "synonyms": {
                "service user": 1,
                "serviceuser": 1,
                "client": 1,

                "staff": 2,
                "employee": 2,

                "family": 3,
                "family member": 3,
                "relative": 3,

                "professional": 4,
                "health professional": 4,
                "care professional": 4,

                "social worker": 5,
                "socialworker": 5,

                "commissioner": 6,

                "public": 7,
                "general public": 7,

                "other stakeholder": 8,
                "otherstakeholder": 8,
                "stakeholder": 8,
                "other": 8
            },
        },
    },
 
    # ── BNR_ComplaintOrConcernOrComplementReport ─────────────────────────────
    "BNR_ComplaintOrConcernOrComplementReport": {
        "StatusOfComplaintOrConcern": {
            "db_type": "int",
            "synonyms": {
                "upheld": 1,
                "partially upheld": 2,
                "partial": 2,
                "not upheld": 3,
                "rejected": 3,
                "not resolved": 4,
                "unresolved": 4,
            },
        },
        "CategoriesFallUnderComplaintReport": {
            "db_type": "int",
            "synonyms": {
                "protected characteristic": 1,
                "aggression": 2,
                "staff performance": 3,
                "performance": 3,
                "staff attitude": 4,
                "attitude": 4,
                "staff knowledge": 5,
                "knowledge": 5,
                "abuse": 6,
                "neglect": 7,
                "unkempt property": 8,
                "unkempt": 8,
                "clean property": 9,
                "cleanliness": 9,
                "service user improvement": 10,
                "improvement": 10,
                "service users behaviour": 11,
                "behaviour": 11,
                "other": 12,
            },
        },
    },
 
    # ── BNR_ComplaintOrConcernOrComplementWitness ────────────────────────────
    "BNR_ComplaintOrConcernOrComplementWitness": {
        "WitnessUserType": {
            "db_type": "int",
            "synonyms": {
                "self": 1,
                "service user": 2,
                "client": 2,
                "staff": 3,
                "employee": 3,
                "other": 4,
            },
        },
    },
 
    # ── BNR_ComplaintRelatedPerson ───────────────────────────────────────────
    "BNR_ComplaintRelatedPerson": {
        "ComplaintRelatedToPerson": {
            "db_type": "int",
            "synonyms": {
                "service user": 1,
                "serviceuser": 1,
                "client": 1,

                "staff": 2,
                "employee": 2,

                "family": 3,
                "family member": 3,
                "relative": 3,

                "professional": 4,
                "health professional": 4,
                "care professional": 4,

                "social worker": 5,
                "socialworker": 5,

                "commissioner": 6,

                "public": 7,
                "general public": 7,

                "other stakeholder": 8,
                "otherstakeholder": 8,
                "stakeholder": 8,
                "other": 8
            },
        },
    },
 
    # ── BNR_RiskAssessment ───────────────────────────────────────────────────
    "BNR_RiskAssessment": {
        "RiskType": {
            "db_type": "int",
            "synonyms": {
                "employee": 1,
                "staff": 1,
                "other user": 2,
                "other": 2,
                "service user": 3,
                "client": 3,
            },
        },
        "ReferenceFormType": {
            "db_type": "int",
            "synonyms": {
                "risk assessment": 1,
                "service user support plan": 2,
                "support plan": 2,
                "positive behaviour support plan": 3,
                "positive behaviour": 3,
                "both": 4,
            },
        },
    },
 
    # ── BNR_RiskAsseOtherUser ────────────────────────────────────────────────
    "BNR_RiskAsseOtherUser": {
        "RiskAsseOtherUserType": {
            "db_type": "int",
            "synonyms": {
                "visitor": 1,
                "guest": 1,
                "general public": 2,
                "public": 2,
                "other": 3,
            },
        },
    },
 
    # ── BNR_FinanceRecord ────────────────────────────────────────────────────
    "BNR_FinanceRecord": {
        "FinanceFormAction": {
            "db_type": "int",
            "synonyms": {
                "withdraw": 1,
                "withdrawal": 1,
                "allocate against withdraw": 2,
                "add": 3,
                "deposit": 3,
                "top up": 3,
                "straight purchase": 4,
                "purchase": 4,
                "balance check": 5,
                "check balance": 5,
                "unknown transaction": 6,
                "unknown": 6,
                "allocate against unknown transaction": 7,
                "allocate against unknown": 7,
            },
        },
        "PurposeForWithdraw": {
            "db_type": "int",
            "synonyms": {
                "shopping": 1,
                "outing": 2,
                "trip": 2,
                "holiday": 3,
                "bills": 4,
                "fuel": 5,
                "repairs": 6,
                "vehicle": 7,
                "other activities": 8,
                "other": 8,
            },
        },
        "PurposeForAdd": {
            "db_type": "int",
            "synonyms": {
                "top up": 1,
                "from self": 2,
                "self": 2,
                "from organisation": 3,
                "organisation": 3,
                "from family": 4,
                "family": 4,
                "from other": 5,
                "other": 5,
                "balance correction": 6,
                "correction": 6,
            },
        },
    },
 
    # ── BNR_FinanceRecordAllocationAgainstWithdraw ───────────────────────────
    "BNR_FinanceRecordAllocationAgainstWithdraw": {
        "PurposeForAllocateAgainstWithdraw": {
            "db_type": "int",
            "synonyms": {
                "grocery": 1,
                "groceries": 1,
                "takeaway": 2,
                "gas": 3,
                "electricity": 4,
                "water": 5,
                "broadband": 6,
                "internet": 6,
                "phone": 7,
                "household items": 8,
                "household": 8,
                "transport": 9,
                "bowling": 10,
                "football": 11,
                "bingo": 12,
                "boxing": 13,
                "swimming": 14,
                "change against withdraw": 15,
                "change": 15,
            },
        },
        "FinanceFormAllocationType": {
            "db_type": "int",
            "synonyms": {
                "allocate against withdraw": 1,
                "allocate against unknown": 2,
                "unknown": 2,
            },
        },
        "PurposeForAllocateAgainstUnknown": {
            "db_type": "int",
            "synonyms": {
                "add": 1,
                "allocate against unknown": 2,
                "change against unknown": 3,
                "change": 3,
            },
        },
    },
 
    # ── BNR_FoodAndFluidChart ────────────────────────────────────────────────
    "BNR_FoodAndFluidChart": {
        "MealDrinkOrSnackType": {
            "db_type": "int",
            "synonyms": {
                "breakfast": 1,
                "morning meal": 1,
                "mid morning": 2,
                "lunch": 3,
                "mid afternoon": 4,
                "afternoon snack": 4,
                "dinner": 5,
                "evening meal": 5,
                "supper": 6,
                "night time": 7,
                "night": 7,
                "snack": 8,
                "drink": 9,
                "beverage": 9,
            },
        },
        "IndividualConsumptionType": {
            "db_type": "int",
            "synonyms": {
                "cereal": 1,
                "porridge": 2,
                "milk and sugar": 3,
                "milk": 3,
                "cooked items": 4,
                "cooked": 4,
                "bread and toast": 5,
                "bread": 5,
                "toast": 5,
                "spread": 6,
                "drinks": 7,
                "snack": 8,
                "sandwich": 9,
                "soup": 10,
                "main item": 11,
                "main": 11,
                "fruit": 12,
                "pudding": 13,
                "dessert": 13,
                "other": 14,
            },
        },
        "SizeOfPortion": {
            "db_type": "int",
            "synonyms": {
                "small": 1,
                "medium": 2,
                "large": 3,
                "big": 3,
            },
        },
        "AmountEatenType": {
            "db_type": "int",
            "synonyms": {
                "none": 1,
                "nothing": 1,
                "quarter": 2,
                "half": 3,
                "three quarters": 4,
                "three quarter": 4,
                "all": 5,
                "everything": 5,
                "full": 5,
            },
        },
        "SelectionOfChoiceType": {
            "db_type": "int",
            "synonyms": {
                "did they ask": 1,
                "asked": 1,
                "they were offered": 2,
                "offered": 2,
                "just given": 3,
                "given": 3,
            },
        },
    },
 
    # ── BNR_SleepChart ───────────────────────────────────────────────────────
    "BNR_SleepChart": {
        "SleepState": {
            "db_type": "int",
            "synonyms": {
                "appears asleep": 1,
                "asleep": 1,
                "sleeping": 1,
                "awake": 2,
                "awoken": 2,
                "up": 2,
            },
        },
        "SleepLocation": {
            "db_type": "int",
            "synonyms": {
                "lounge": 1,
                "living room": 1,
                "kitchen": 2,
                "bedroom": 3,
                "bed": 3,
                "corridor": 4,
                "hallway": 4,
                "another area": 5,
                "other area": 5,
                "outside the house": 6,
                "outside": 6,
                "other": 7,
            },
        },
    },
 
    # ── BNR_ABCFormBehaviour ─────────────────────────────────────────────────
    "BNR_ABCFormBehaviour": {
        "BehaviourType": {
            "db_type": "int",
            "synonyms": {
                "biting": 1,
                "bite": 1,
                "pinching": 2,
                "pinch": 2,
                "screaming": 3,
                "scream": 3,
                "shouting": 4,
                "shout": 4,
                "yelling": 4,
                "banging": 5,
                "bang": 5,
                "head banging": 6,
                "hitting self": 7,
                "self hit": 7,
                "hitting staff": 8,
                "hit staff": 8,
                "sexually inappropriate behaviours": 9,
                "sexually inappropriate": 9,
                "sexual behaviour": 9,
                "grabbing": 10,
                "grab": 10,
                "destroying property": 11,
                "property destruction": 11,
                "scratching": 12,
                "scratch": 12,
                "spitting": 13,
                "spit": 13,
                "kicking": 14,
                "kick": 14,
                "inappropriate language": 15,
                "bad language": 15,
                "racially abusive": 16,
                "racial abuse": 16,
                "racism": 16,
                "self injurious behaviours": 17,
                "self injury": 17,
                "self injurious": 17,
                "exposing themselves to danger": 18,
                "danger": 18,
                "self harm": 19,
                "selfharm": 19,
                "other": 20,
            },
        },
    },
 
    # ── BNR_Counselling ──────────────────────────────────────────────────────
    "BNR_Counselling": {
        "discussionType": {
            "db_type": "int",
            "synonyms": {
                "counselling": 1,
                "counseling": 1,
                "improvement": 2,
                "excellent": 3,
                "excellence": 3,
            },
        },
    },
 
    # ── BNR_Course ───────────────────────────────────────────────────────────
    "BNR_Course": {
        "CourseType": {
            "db_type": "int",
            "synonyms": {
                "text": 1,
                "written": 1,
                "reading": 1,
                "video": 2,
            },
        },
        "InternalAndExternalCourseType": {
            "db_type": "int",
            "synonyms": {
                "internal": 1,
                "chronoplot internal": 1,
                "face to face": 2,
                "face2face": 2,
                "in person": 2,
                "online external": 3,
                "online": 3,
                "external": 3,
            },
        },
    },
 
    # ── BNR_Questions ────────────────────────────────────────────────────────
    "BNR_Questions": {
        "QuestionType": {
            "db_type": "int",
            "synonyms": {
                "single": 1,
                "single choice": 1,
                "multiple": 2,
                "multiple choice": 2,
                "multi": 2,
                "text": 3,
                "free text": 3,
            },
        },
    },
 
    # ── BNR_AuditActionPlan ──────────────────────────────────────────────────
    "BNR_AuditActionPlan": {
        "TypeOfActionPlan": {
            "db_type": "int",
            "synonyms": {
                "auditors": 1,
                "auditor": 1,
                "managers": 2,
                "manager": 2,
            },
        },
    },
 
    # ── BNR_AuditMasterSetting ───────────────────────────────────────────────
    "BNR_AuditMasterSetting": {
        "TypeOfAudit": {
            "db_type": "int",
            "synonyms": {
                "current month": 1,
                "this month": 1,
                "next month": 2,
                "upcoming month": 2,
            },
        },
    },
 
    # ── BNR_CostingCalculatorCommunityAccessEntry ────────────────────────────
    "BNR_CostingCalculatorCommunityAccessEntry": {
        "Ratio": {
            "db_type": "int",
            "synonyms": {
                "1:1": 1,
                "one to one": 1,
                "2:1": 2,
                "two to one": 2,
                "3:1": 3,
                "three to one": 3,
                "4:1": 4,
                "four to one": 4,
                "5:1": 5,
                "five to one": 5,
                "6:1": 6,
                "six to one": 6,
            },
        },
    },
 
    # ── BNR_CostingCalculatorSpotHourEntry ───────────────────────────────────
    "BNR_CostingCalculatorSpotHourEntry": {
        "Ratio": {
            "db_type": "int",
            "synonyms": {
                "1:1": 1,
                "one to one": 1,
                "2:1": 2,
                "two to one": 2,
                "3:1": 3,
                "three to one": 3,
                "4:1": 4,
                "four to one": 4,
                "5:1": 5,
                "five to one": 5,
                "6:1": 6,
                "six to one": 6,
            },
        },
    },
 
    # ── BNR_CostingCalculatorWakingNightPerHourEntry ─────────────────────────
    "BNR_CostingCalculatorWakingNightPerHourEntry": {
        "Ratio": {
            "db_type": "int",
            "synonyms": {
                "1:1": 1,
                "one to one": 1,
                "2:1": 2,
                "two to one": 2,
                "3:1": 3,
                "three to one": 3,
                "4:1": 4,
                "four to one": 4,
                "5:1": 5,
                "five to one": 5,
                "6:1": 6,
                "six to one": 6,
            },
        },
    },
 
    # ── BNR_VisitLog ─────────────────────────────────────────────────────────
    "BNR_VisitLog": {
        "LogType": {
            "db_type": "int",
            "synonyms": {
                "navigation": 1,
                "navigate": 1,
                "page view": 1,
                "action": 2,
                "click": 2,
            },
        },
    },
 
    # ── BNR_Archived_VisitLog ────────────────────────────────────────────────
    "BNR_Archived_VisitLog": {
        "LogType": {
            "db_type": "int",
            "synonyms": {
                "navigation": 1,
                "navigate": 1,
                "page view": 1,
                "action": 2,
                "click": 2,
            },
        },
    },
 
    # ── BNR_CommunicationDictionary ──────────────────────────────────────────
    "BNR_CommunicationDictionary": {
        "CommunicationPassportWhatISaidOrDid": {
            "db_type": "int",
            "synonyms": {
                "what i said": 1,
                "said": 1,
                "what i did": 2,
                "did": 2,
            },
        },
    },
 
    # ── BNR_AboutMeServiceUserInvolvedPerson ─────────────────────────────────
    "BNR_AboutMeServiceUserInvolvedPerson": {
        "InvolvedPersonType": {
            "db_type": "int",
            "synonyms": {
                "self": 1,
                "service user": 2,
                "client": 2,
                "staff": 3,
                "employee": 3,
                "other": 4,
                "family": 5,
            },
        },
    },
 
    # ── BNR_CommunicationPassportInvolvedPerson ──────────────────────────────
    "BNR_CommunicationPassportInvolvedPerson": {
        "InvolvedPersonType": {
            "db_type": "int",
            "synonyms": {
                "self": 1,
                "service user": 2,
                "client": 2,
                "staff": 3,
                "employee": 3,
                "other": 4,
                "family": 5,
            },
        },
    },
 
    # ── BNR_PositiveBehaviourSupportPlan ─────────────────────────────────────
    "BNR_PositiveBehaviourSupportPlan": {
        "ReferenceFormType": {
            "db_type": "int",
            "synonyms": {
                "risk assessment": 1,
                "service user support plan": 2,
                "support plan": 2,
                "positive behaviour support plan": 3,
                "positive behaviour": 3,
                "both": 4,
            },
        },
    },
 
    # ── BNR_ServiceUserSupportPlan ───────────────────────────────────────────
    "BNR_ServiceUserSupportPlan": {
        "ReferenceFormType": {
            "db_type": "int",
            "synonyms": {
                "risk assessment": 1,
                "service user support plan": 2,
                "support plan": 2,
                "positive behaviour support plan": 3,
                "positive behaviour": 3,
                "both": 4,
            },
        },
    },
 
    # ── BNR_UserDetailSupervisionActionAndTargets ────────────────────────────
    "BNR_UserDetailSupervisionActionAndTargets": {
        "ResponsiblePersonType": {
            "db_type": "int",
            "synonyms": {
                "self": 1,
                "service user": 2,
                "client": 2,
                "staff": 3,
                "employee": 3,
                "other": 4,
                "family": 5,
            },
        },
    },
 
    # ── BNR_UserDetailSupervisionFeedback ────────────────────────────────────
    "BNR_UserDetailSupervisionFeedback": {
        "SenderRole": {
            "db_type": "int",
            "synonyms": {
                "self": 1,
                "service user": 2,
                "client": 2,
                "staff": 3,
                "employee": 3,
                "other": 4,
                "family": 5,
            },
        },
    },
 
    # ── BNR_PersonToInterviewAndSupplyStatement ──────────────────────────────
    "BNR_PersonToInterviewAndSupplyStatement": {
        "PersonToInterviewOrSupplyOwnStatement": {
            "db_type": "int",
            "synonyms": {
                "person to interview": 1,
                "interview": 1,
                "person to supply statement": 2,
                "statement": 2,
            },
        },
        "SelectedUserType": {
            "db_type": "int",
            "synonyms": {
                "self": 1,
                "service user": 2,
                "client": 2,
                "staff": 3,
                "employee": 3,
                "other": 4,
                "family": 5,
            },
        },
    },
 
    # ── BNR_PersonInterviewedAndNonInterviewedPerson ─────────────────────────
    "BNR_PersonInterviewedAndNonInterviewedPerson": {
        "PersonInterviewed": {
            "db_type": "int",
            "synonyms": {
                "persons interviewed": 1,
                "interviewed": 1,
                "person not interviewed": 2,
                "not interviewed": 2,
            },
        },
        "PersonInterviewedOrNot": {
            "db_type": "int",
            "synonyms": {
                "persons interviewed": 1,
                "interviewed": 1,
                "person not interviewed": 2,
                "not interviewed": 2,
            },
        },
    },
 
    # ── BNR_ProcessAndReportInvolvedPerson ───────────────────────────────────
    "BNR_ProcessAndReportInvolvedPerson": {
        "SelectedUserType": {
            "db_type": "int",
            "synonyms": {
                "self": 1,
                "service user": 2,
                "client": 2,
                "staff": 3,
                "employee": 3,
                "other": 4,
                "family": 5,
            },
        },
    },
 
    # ── BNR_SafeguardingInvestigationPlanning ────────────────────────────────
    "BNR_SafeguardingInvestigationPlanning": {
        "InverstigationReportForUserType": {
            "db_type": "int",
            "synonyms": {
                "self": 1,
                "service user": 2,
                "client": 2,
                "staff": 3,
                "employee": 3,
                "other": 4,
                "family": 5,
            },
        },
    },
 
    # ── BNR_SafeguardingInvestigationPlanningSupportingDocument ─────────────
    "BNR_SafeguardingInvestigationPlanningSupportingDocument": {
        "UploadedDocumentType": {
            "db_type": "int",
            "synonyms": {
                "saf investigation supporting document": 1,
                "saf supporting document": 1,
                "saf": 1,
                "investigation document": 2,
                "investigation": 2,
            },
        },
    },
 
    # ── BNR_ComplaintOrConcernOrComplementReportProposedActionPlan ───────────
    "BNR_ComplaintOrConcernOrComplementReportProposedActionPlan": {
        "ProposedActionPlanByUserType": {
            "db_type": "int",
            "synonyms": {
                "self": 1,
                "service user": 2,
                "client": 2,
                "staff": 3,
                "employee": 3,
                "other": 4,
                "family": 5,
            },
        },
    },
    "BNR_ComplaintOrConcernOrComplementReceivingPersons" : {
        "MemberOfStaffReceivingComplaint": {
            "db_type": "int",
            "synonyms": {
                "self": 1,
                "myself": 1,
                "own": 1,

                "service user": 2,
                "serviceuser": 2,
                "client": 2,

                "staff": 3,
                "employee": 3,
                "staff member": 3,

                "other": 4,
                "others": 4
            }
        }
    },
    "BNR_InvestigatorAndInvestigationCompleted" : {
        "InvestigatorOrInvestigationCompletedBy": {
            "db_type": "int",
            "synonyms": {
                "investigator": 1,
                "investigation officer": 1,
                "assigned investigator": 1,

                "investigation completed by": 2,
                "investigation meeting completed by": 2,
                "completed by": 2,
                "meeting completed by": 2
            }
        },
        "SelectedUserType": {
            "db_type": "int",
            "synonyms": {
                "self": 1,
                "myself": 1,
                "own": 1,

                "service user": 2,
                "serviceuser": 2,
                "client": 2,

                "staff": 3,
                "employee": 3,
                "staff member": 3,

                "other": 4,
                "others": 4,

                "family": 5,
                "family member": 5,
                "relative": 5
            }
        }
    },
    "BNR_InvestigationAndInvestigator" : {
        "InvestigationOrInvestigator": {
            "db_type": "int",
            "synonyms": {
                "investigation authorised": 1,
                "investigation authorized": 1,
                "authorised": 1,
                "authorized": 1,

                "investigator": 2,
                "investigation officer": 2
            }
        },
        "SelectedUser": {
            "db_type": "int",
            "synonyms": {
                "self": 1,
                "myself": 1,
                "own": 1,

                "service user": 2,
                "serviceuser": 2,
                "client": 2,

                "staff": 3,
                "employee": 3,
                "staff member": 3,

                "other": 4,
                "others": 4,

                "family": 5,
                "family member": 5,
                "relative": 5
            }
        }
    },
    "BNR_BowelMovementChart" : {
        "StoolSize": {
            "db_type": "int",
            "synonyms": {
                "small": 1,
                "little": 1,
                "compact": 1,

                "medium": 2,
                "average": 2,
                "normal": 2,

                "large": 3,
                "big": 3,
                "extra large": 3,
                "xl": 3
            }
        }
    },
    "BNR_RecruitmentChecklistDetails" : {
        "RecruitmentChecklistDetailsType": {
            "db_type": "int",
            "synonyms": {
                "interview questions": 1,
                "interview": 1,

                "application form": 2,
                "application": 2,

                "health declaration": 3,
                "pre employment health declaration": 3,

                "equality monitoring": 4,

                "48 hour opt out": 5,
                "forty eight hour opt out": 5,

                "training agreement": 6,

                "family relationship declaration": 7,
                "family declaration": 7,

                "visa declaration": 8,
                "visa": 8,

                "hmrc checklist": 9,
                "hmrc": 9,

                "bank details": 10,
                "bank info": 10,

                "application link sent": 11,

                "verify id": 12,
                "authorise id": 12,
                "verify authorise id": 12,

                "dbs disclosure received": 13,
                "dbs received": 13,

                "original dbs certificate checked": 14,
                "dbs certificate checked": 14,

                "update service check": 15,

                "risk assessment completed": 16,

                "position of risk assessor": 17,
                "risk assessor position": 17,

                "chronoplot login details": 18,
                "chronoplot login": 18,

                "social care tv login": 19,

                "qcs login": 20,

                "registration email": 21,

                "group documents": 22,
                "policies procedures handbook": 22,

                "international staff member": 23,
                "sponsorship": 23,

                "access to services sites": 24,
                "system access": 24,

                "staff added to trackers": 25,
                "added to trackers": 25,

                "mandatory training completed": 26,
                "training completed": 26,

                "induction pack given": 27,
                "induction": 27,

                "job description and offer letter": 28,
                "offer letter": 28,

                "signed contract": 29,
                "contract signed": 29,

                "full recruitment pack scanned": 30,
                "documents uploaded": 30,

                "details reflected on bhr information": 31,
                "bhr details updated": 31
            }
        }
    },
    "BNR_RecruitmentChecklistReferences" : {
        "ReferenceType": {
            "db_type": "int",
            "synonyms": {
                "first professional": 1,
                "professional reference 1": 1,
                "first reference": 1,

                "second professional": 2,
                "professional reference 2": 2,
                "second reference": 2,

                "character reference": 3,
                "character": 3,
                "personal reference": 3,

                "other": 4,
                "others": 4
            }
        }
    },
    "BNR_CostingCalculatorHourDetail" : {
        "Day": {
            "db_type": "int",
            "synonyms": {
                "monday": 1,
                "tuesday": 2,
                "wednesday": 3,
                "thursday": 4,
                "friday": 5,
                "saturday": 6,
                "sunday": 0,
            }
        }
    }
}
 
 
# ---------------------------------------------------------------------------
# Resolver — filters registry to only tables selected by planner
# ---------------------------------------------------------------------------
 
def get_relevant_enums(relevant_tables: list) -> dict:
    """
    Takes plan.relevant_tables (list of TableContext objects with .table_name),
    returns only the enum entries for tables actually in the query plan.
    """
    selected = {t.table_name for t in relevant_tables}
    return {
        table: columns
        for table, columns in ENUM_REGISTRY.items()
        if table in selected
    }
 
 
# ---------------------------------------------------------------------------
# Prompt builder — formats enum map into generator prompt block
# ---------------------------------------------------------------------------
 
def build_enum_prompt_block(relevant_enums: dict) -> str:
    """
    Formats the filtered enum map into a clean prompt block for the generator.
    Returns empty string if no relevant enums found (no injection needed).
    """
    if not relevant_enums:
        return ""
 
    lines = [
        "=====================",
        "ENUM VALUE MAP — INTEGER VALUES STORED IN DB",
        "=====================",
        "These columns store INTEGER values, NOT strings.",
        "Match the user's natural language to the exact integer shown below.",
        "ALWAYS use = or IN with the integer. NEVER use LIKE. NEVER use the text label.\n",
    ]
 
    for table, columns in relevant_enums.items():
        lines.append(f"Table: {table}")
        for col, meta in columns.items():
            lines.append(f"  Column: {col}  [{meta['db_type']}]")
            # Group synonyms by their integer value for clean display
            # Normalize keys: lowercase + replace underscores with spaces
            value_to_synonyms: Dict[int, List[str]] = {}
            for synonym, value in meta["synonyms"].items():
                normalized = synonym.lower().replace("_", " ")
                value_to_synonyms.setdefault(value, []).append(normalized)
            # Deduplicate after normalization
            for value in sorted(value_to_synonyms.keys()):
                seen = set()
                unique_synonyms = []
                for s in value_to_synonyms[value]:
                    if s not in seen:
                        seen.add(s)
                        unique_synonyms.append(s)
                syn_str = ", ".join(f'"{s}"' for s in unique_synonyms)
                lines.append(f"    {syn_str}  →  {value}")
        lines.append("")
 
    lines += [
        "RULES:",
        "- ALWAYS use the integer value, NEVER the text label",
        "- Single match  → use =   e.g. WHERE Status = 1",
        "- Multi match   → use IN  e.g. WHERE PersonAffected IN (1, 2)",
        "- NEVER write WHERE Status = 'Active'        ← string value — WRONG",
        "- NEVER write WHERE Status LIKE '%active%'   ← LIKE on enum — WRONG",
        "- ALWAYS write WHERE Status = 1              ← integer value — CORRECT",
        "- If user's word has no match in the map above, skip that filter entirely",
        "- NEVER generate a filter with an empty or guessed value",
        "",
        "⛔ CRITICAL — ENUM COLUMNS ARE NOT FOREIGN KEYS:",
        "- NEVER use an enum column in a JOIN condition",
        "- Example: PersonAffected stores 1=Service User, 2=Staff — it is NOT a FK to any person table",
        "- WRONG: JOIN BNR_Service_User t2 ON t1.PersonAffected = t2.Id",
        "- RIGHT:  WHERE t1.PersonAffected = 1  (use as a WHERE filter only)",
        "",
        "COMBINING ENUM FILTER WITH PERSON NAME FILTER:",
        "- If the user mentions a person type (e.g. 'service user') AND a person name (e.g. 'Vikas Kohli'):",
        "  Step 1 → Add enum WHERE filter: WHERE t1.PersonAffected = 1",
        "  Step 2 → JOIN the resolved person table using the ACTUAL FK column from schema",
        "  Step 3 → Add name filter on the joined table: AND t2.FirstName LIKE '%Vikas%'",
        "- The enum filter and the name JOIN are two separate things — never confuse them",
        "=====================",
    ]
 
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Reverse map for output formatting
# ---------------------------------------------------------------------------

def reverse_map_enums(rows: List[Dict[str, Any]], relevant_tables: list) -> List[Dict[str, Any]]:
    """
    Given SQL result rows, converts integer enum values back to their 
    human-readable string representations based on the relevant tables.
    """
    if not rows or not relevant_tables:
        return rows

    def normalize(val: str) -> str:
        return str(val).lower().replace("_", "").replace(" ", "")

    # Build a consolidated map from ENUM_REGISTRY for the columns present in result rows
    reverse_map = {}
    selected_normalized = {normalize(t.table_name) for t in relevant_tables}
    
    logger.info(f"[reverse_map] Selected tables (normalized): {selected_normalized}")

    for table_name, columns in ENUM_REGISTRY.items():
        norm_table = normalize(table_name)
        if norm_table in selected_normalized:
            logger.info(f"[reverse_map] Found match for table: {table_name}")
            for col_name, meta in columns.items():
                norm_col = normalize(col_name)
                if norm_col not in reverse_map:
                    reverse_map[norm_col] = {}
                for synonym, val in meta["synonyms"].items():
                    if val not in reverse_map[norm_col]:
                        clean = synonym.replace('_', ' ').title()
                        reverse_map[norm_col][val] = clean
                        # Also store as string key for robustness
                        reverse_map[norm_col][str(val)] = clean

    if not reverse_map:
        logger.info("[reverse_map] No enum columns found for the selected tables.")
        return rows

    logger.info(f"[reverse_map] Built reverse map for columns: {list(reverse_map.keys())}")

    new_rows = []
    for row in rows:
        new_row = dict(row)
        for k, v in new_row.items():
            norm_k = normalize(k)
            if norm_k in reverse_map:
                # Try original value, then stringified version
                if v in reverse_map[norm_k]:
                    new_row[k] = reverse_map[norm_k][v]
                elif str(v) in reverse_map[norm_k]:
                    new_row[k] = reverse_map[norm_k][str(v)]
        new_rows.append(new_row)
    return new_rows
