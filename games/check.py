import re


def clean(func):
   def func_wrapper(self, text):
       return text.to_lower().strip()
   return func_wrapper


def delete_spaces(func):
   def func_wrapper(self, text):
       return re.sub(r"\s+", "", text)
   return func_wrapper



class BaseChecker:
    def __init__(self, data):
        pass

    def check(self, text):
        pass


class SimpleBoolChecker(BaseChecker):
    def bool_check(self, text):
        pass

    def check(self, text):
        is_ok = self.bool_check(text)
        if is_ok:
            return 'Ok', 1
        else:
            return 'Pending', 0


class EqualsChecker(SimpleBoolChecker):
    @clean
    def __init__(self, data):
        self.data = data

    @clean
    def bool_check(self, text):
        return text == self.data


class EqualsWithPossibleSpacesChecker(SimpleBoolChecker):
    @clean
    @delete_spaces
    def __init__(self, data):
        self.data = data

    @clean
    @delete_spaces
    def bool_check(self, text):
        bool_result = text == self.data


class CheckerFactory:
    def __init__(self):
        checker_type_to_checker = {
            'equals': EqualsChecker,
            'equals_with_possible_spaces': EqualsWithPossibleSpacesChecker,
        }
    
    def create_checker(self, checker_type, data):
        return checker_type_to_checker[checker_type](data)
