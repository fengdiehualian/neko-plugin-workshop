"""Question policy and delivery, independent of the negotiated game vocabulary."""

from .handoff import make_submission_tool


class ReadOnlyResearchSurface:
    """One current research policy; subclasses supply only game reads/guidance."""


    def __init__(self, setup, session, update):
        self.setup = setup
        self.session = session
        self.update = update
        self.finalizing = False


    def submission(self, *, answer_only=False):
        return make_submission_tool(
            self.session.submission,
            self.session.observations,
            self.update,
            answer_only=answer_only,
        )

    def tools(self):
        submit = self.submission()
        return (submit,) if self.finalizing else (*self.read_tools(), submit)
