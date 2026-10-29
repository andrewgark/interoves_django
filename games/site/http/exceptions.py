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

class ReplayedClientEventException(DuplicateAttemptException):
    """The client event id already produced ``attempt_id`` for this actor."""

    def __init__(self, attempt_id):
        super().__init__('client event was already processed')
        self.attempt_id = attempt_id

class ClientEventConflictException(Exception):
    """The client event id belongs to another actor or task."""
