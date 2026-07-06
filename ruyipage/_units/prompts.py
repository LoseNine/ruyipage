# -*- coding: utf-8 -*-


class PromptsManager(object):
    """Small compatibility facade for user prompt handling."""

    def __init__(self, owner):
        self._owner = owner

    def set_auto(self, accept=True, text=None):
        """Automatically handle alert/confirm/prompt dialogs.

        Args:
            accept: True accepts prompts, False dismisses them.
            text: Optional text to enter for prompt dialogs when accepting.

        Returns:
            PromptsManager: self, for chaining.
        """
        action = "accept" if accept else "dismiss"
        self._owner.set_prompt_handler(
            alert=action,
            confirm=action,
            prompt=action,
            default=action,
            prompt_text=text,
        )
        return self

    def accept(self, text=None):
        """Shortcut for ``set_auto(accept=True, text=text)``."""
        return self.set_auto(accept=True, text=text)

    def dismiss(self):
        """Shortcut for ``set_auto(accept=False)``."""
        return self.set_auto(accept=False)

    def clear(self):
        self._owner.clear_prompt_handler()
        return self

    def stop(self):
        return self.clear()

    def wait(self, timeout=3):
        return self._owner.wait_prompt(timeout=timeout)

    def current(self):
        return self._owner.get_user_prompt()

    def last_opened(self):
        return self._owner.get_last_prompt_opened()

    def last_closed(self):
        return self._owner.get_last_prompt_closed()

    def handle(self, accept=True, text=None, timeout=3):
        self._owner.handle_prompt(accept=accept, text=text, timeout=timeout)
        return self

    def respond(self, accept=True, text=None, timeout=3):
        return self.handle(accept=accept, text=text, timeout=timeout)

    def accept_current(self, text=None, timeout=3):
        return self.handle(accept=True, text=text, timeout=timeout)

    def dismiss_current(self, timeout=3):
        return self.handle(accept=False, timeout=timeout)

    def input(self, text, timeout=3):
        return self.handle(accept=True, text=text, timeout=timeout)
