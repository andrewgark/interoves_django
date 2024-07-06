import json
from django.http import JsonResponse


def countries_task_100(request):
    key = request.GET.get('key', None)
    if key is None:
        return JsonResponse({'error': "Parameter 'key' is missing"})
    set_allowed_letters = set(list('ABCDEFGHIJKLMNOPQRSTUVWXYZ,'))
    for letter in key:
        if letter not in set_allowed_letters:
            return JsonResponse({'error': 'Found forbidden symbol: {}'.format(letter)})
    if len(key) % 3 != 2:
        return JsonResponse({'error': 'Forbidden number of symbols: {}'.format(len(key))})
    for i, letter in enumerate(key):
        if (i % 3 == 2) and (letter != ','):
            return JsonResponse({'error': "Symbol number {} should be ',', not {}".format(i + 1, letter)})
        if (i % 3 != 2) and (letter == ','):
            return JsonResponse({'error': "Symbol number {} should not be ','".format(i + 1)})
    country_to_neighbors = json.load(open('games/data/borders.json'))
    countries = []
    for i in range(0, len(key), 3):
        country_code = key[i:i+2]
        if country_code not in country_to_neighbors:
            return JsonResponse({'error': '{} is not a correct value'.format(country_code)})
        countries.append(country_code)
    first_country = countries[0]
    for country in countries[1:]:
        if country not in country_to_neighbors[first_country]:
            return JsonResponse({'error': '{} should be a neighbor of {}'.format(country, first_country)})
    if len(country_to_neighbors[first_country]) != (len(countries) - 1): 
        return JsonResponse({'error': 'You need to include every neighbor of {}. At least one neighbor is missing.'.format(first_country)})
    landlocked_countries = set(json.load(open('games/data/landlocked_countries.json')))
    for country in countries:
        if country not in landlocked_countries:
            return JsonResponse({'error': 'Country {} should not have access to the ocean'.format(country)})
    if first_country == 'LI':
        return JsonResponse({'status': 'OK', 'code': '1_LIECHTENSTEIN_PERFECT', 'comment': 'Submit this code as answer to 2.1 to get 5 points. There is also another correct answer (2.2)'})
    if first_country == 'UZ':
        return JsonResponse({'status': 'OK', 'code': '2_UZBEKISTAN_WELL_DONE', 'comment': 'Submit this code as answer to 2.2 to get 5 points. There is also another correct answer (2.1)'})
    return JsonResponse({'error': 'unintended error, please write @andrewgark about this error'})


def capitals_task_100(request):
    key = request.GET.get('key', None)
    if key is None:
        return JsonResponse({'error': "Parameter 'key' is missing"})
    set_allowed_letters = set(list('абвгдеёжзийклмнопрстуфхцчшщъыьэюя-'))
    for letter in key:
        if letter not in set_allowed_letters:
            return JsonResponse({'error': 'Found forbidden symbol: {}'.format(letter)})
    if len(key) > 23:
        return JsonResponse({'error': 'Too many symbols: {}'.format(len(key))})
    if len(key) == 0:
        return JsonResponse({'error': 'There should be at least one symbol'})
    try:
        capitals = set(json.load(open('games/data/capitals.json')))
    except:
        return JsonResponse({'error': 'cant read json file, please write @andrewgark about this error'})
    found_capitals = []
    for capital in capitals:
        if capital in key:
            found_capitals.append(capital)
    if len(found_capitals) == 0:
        return JsonResponse({'error': 'Found capitals: 0. There should be more capitals.'})
    if len(found_capitals) < 6:
        return JsonResponse({'error': 'Found capitals: {}. There should be more capitals.'.format(
            len(found_capitals)
        )})
    if len(key) <= 17:
        return JsonResponse({'status': 'OK', 'code': 'CAPITALS_WIN_WIN_WIN', 'comment': 'This code is worth 10/10 points.'})
    if len(key) == 18:
        return JsonResponse({'status': 'OK', 'code': 'CAPITALS_18_NICE', 'comment': 'This code is worth 6/10 points. It is possible to find shorter key to get better code.'})
    if len(key) == 19:
        return JsonResponse({'status': 'OK', 'code': 'CAPITALS_19_PRETTY', 'comment': 'This code is worth 5/10 points. It is possible to find shorter key to get better code.'})
    if len(key) == 20:
        return JsonResponse({'status': 'OK', 'code': 'CAPITALS_20_VERY_GOOD', 'comment': 'This code is worth 4/10 points. It is possible to find shorter key to get better code.'})
    if len(key) == 21:
        return JsonResponse({'status': 'OK', 'code': 'CAPITALS_21_COMPETENT', 'comment': 'This code is worth 3/10 points. It is possible to find shorter key to get better code.'})
    if len(key) == 22:
        return JsonResponse({'status': 'OK', 'code': 'CAPITALS_22_YOU_ARE_THE_BEST', 'comment': 'This code is worth 2/10 points. It is possible to find shorter key to get better code.'})
    if len(key) == 23:
        return JsonResponse({'status': 'OK', 'code': 'CAPITALS_23_YEAH', 'comment': 'This code is worth 1/10 points. It is possible to find shorter key to get better code.'})
    return JsonResponse({'error': 'unintended error, please write @andrewgark about this error'})
