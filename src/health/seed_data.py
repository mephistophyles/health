"""Neutral seed content.

No real household member appears here. Names, notes, likes and dislikes are placeholders
that get edited in the app on first use, or supplied at deploy time through the
`SEED_MEMBERS` and `SEED_USERS` environment variables (see README).
"""

GROCERY_CATEGORIES = [
    'Produce',
    'Bakery',
    'Meat & seafood',
    'Dairy & eggs',
    'Deli',
    'Frozen',
    'Pantry',
    'Canned & jarred',
    'Spices & baking',
    'Drinks',
    'Household',
    'Other',
]

STAPLES = [
    'salt',
    'kosher salt',
    'black pepper',
    'olive oil',
    'vegetable oil',
    'butter',
    'all-purpose flour',
    'sugar',
    'brown sugar',
    'baking powder',
    'baking soda',
    'soy sauce',
    'white vinegar',
    'red wine vinegar',
    'honey',
    'rice',
    'pasta',
    'canned tomatoes',
    'chicken stock',
    'garlic',
    'onion',
]

PLANNING_DEFAULTS = {
    'period_length_days': 7,
    'default_dinner_mode': 'cook',
    'default_lunch_mode': 'none',
    'default_cook_time': 'normal',
    'repeat_weeks': 3,
    'rejection_weeks': 12,
    'major_trip_offset_days': 0,
    'top_up_trip_offset_days': 4,
}

COOK_TIME_LIMITS = {'quick': 30, 'normal': 75, 'project': None}

MONTHLY_SPEND_CAP_USD = 20

REVISE_CHIPS = {
    'meals': ['lighter', 'quicker', 'more kid-friendly', 'cheaper', 'different protein',
              'less equipment', 'more vegetables'],
    'fitness': ['easier', 'harder', 'shorter', 'less equipment', 'joint-friendly',
                'more running', 'more mobility'],
}

# Massachusetts growing season, seeded and editable in settings.
SEASONAL_PRODUCE = {
    1: ['apples', 'cabbage', 'carrots', 'onions', 'parsnips', 'potatoes', 'winter squash'],
    2: ['apples', 'cabbage', 'carrots', 'onions', 'parsnips', 'potatoes', 'winter squash'],
    3: ['apples', 'leeks', 'maple syrup', 'microgreens', 'onions', 'potatoes'],
    4: ['asparagus', 'chives', 'radishes', 'rhubarb', 'spinach', 'spring greens'],
    5: ['asparagus', 'lettuce', 'peas', 'radishes', 'rhubarb', 'spinach', 'strawberries'],
    6: ['beets', 'blueberries', 'broccoli', 'lettuce', 'peas', 'strawberries', 'summer squash'],
    7: ['blueberries', 'corn', 'cucumbers', 'green beans', 'peaches', 'raspberries',
        'summer squash', 'tomatoes'],
    8: ['corn', 'cucumbers', 'eggplant', 'green beans', 'peaches', 'peppers', 'plums',
        'tomatoes', 'zucchini'],
    9: ['apples', 'broccoli', 'cabbage', 'corn', 'grapes', 'kale', 'peppers', 'tomatoes',
        'winter squash'],
    10: ['apples', 'broccoli', 'brussels sprouts', 'cabbage', 'cranberries', 'kale',
         'pumpkin', 'sweet potatoes', 'winter squash'],
    11: ['apples', 'brussels sprouts', 'cabbage', 'carrots', 'cranberries', 'kale',
         'parsnips', 'potatoes', 'winter squash'],
    12: ['apples', 'brussels sprouts', 'cabbage', 'carrots', 'onions', 'potatoes',
         'winter squash'],
}

DEFAULT_MEMBERS = [
    {
        'slug': 'adult-1',
        'name': 'Adult one',
        'kind': 'adult',
        'notes': 'Edit this profile in Settings.',
        'sort_order': 1,
        'is_default_admin': True,
    },
    {
        'slug': 'adult-2',
        'name': 'Adult two',
        'kind': 'adult',
        'notes': 'Edit this profile in Settings.',
        'sort_order': 2,
    },
    {
        'slug': 'child-1',
        'name': 'Child one',
        'kind': 'child',
        'notes': 'Edit this profile in Settings.',
        'sort_order': 3,
        'presentation_rules': {
            'toggles': ['components separated', 'sauce on the side',
                        'one familiar food on the plate'],
            'notes': '',
        },
    },
    {
        'slug': 'child-2',
        'name': 'Child two',
        'kind': 'child',
        'notes': 'Toddler textures and cut sizes; low salt.',
        'sort_order': 4,
        'presentation_rules': {
            'toggles': ['toddler-safe cut sizes', 'low salt', 'soft textures'],
            'notes': 'Avoid common choking hazards: cut rounds lengthways, no whole nuts.',
        },
    },
]

DEFAULT_GOALS = {
    'adult-1': {'kind': 'fitness', 'statement': 'Build strength and stay injury-free.',
                'constraints': ''},
    'adult-2': {'kind': 'fitness', 'statement': 'Exercise regularly and feel stronger.',
                'constraints': ''},
}

DEFAULT_EQUIPMENT = {
    'home': {
        'items': ['full gym access: barbell, rack, dumbbells, machines, cardio equipment'],
        'notes': 'Edit if training at home instead.',
    },
    'travel': {
        'items': ['bodyweight', 'resistance bands', 'running shoes'],
        'notes': 'Assume a basic hotel gym at most.',
    },
}
