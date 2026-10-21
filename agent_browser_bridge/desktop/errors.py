class ActionError(Exception):
    """A desktop action could not be carried out."""


class Aborted(Exception):
    """The operator held Escape, asking for the action to stop."""
