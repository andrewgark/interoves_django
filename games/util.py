def status_key(status):
    status_to_key = {
        'Ok': 3,
        'Partial': 2,
        'Pending': 1,
        'Wrong': 0,
        '': -1,
    }
    return status_to_key[status]


def better_status(first_status, second_status):
    return status_key(first_status) > status_key(second_status)


def clean_text(text, replace_ё=True):
    result = text.lower().strip()
    if replace_ё:
        result = result.replace("ё", "е")
    return result
