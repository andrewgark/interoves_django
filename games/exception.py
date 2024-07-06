class UserHasNoProfileException(Exception):
    pass

class PlayGameWithoutTeamException(Exception):
    pass

class NoGameAccessException(Exception):
    pass

class InvalidFormException(Exception):
    pass

class TooManyAttemptsException(Exception):
    pass

class DuplicateAttemptException(Exception):
    pass

class NotAllRequiredHintsTakenException(Exception):
    pass

class NoAnswerAccessException(Exception):
    pass

class CantRegisterException(Exception):
    pass

class NoTicketsException(Exception):
    pass
