"""Provides objects to provide SMS notification when conversions are complete"""

import logging

from twilio.rest import Client


class Notifier:
    """Notifies a user via SMS using the Twilio API"""

    def __init__(self, account_sid, auth_token, sending_number):
        super().__init__()
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.sending_number = sending_number

    @classmethod
    def create_default_notifier(cls, config):
        """Creates a notifier using the specified notification settings"""

        logger = logging.getLogger()
        if config.notification.sid is None or config.notification.sid == "":
            logger.warning("Twilio account SID not set. Not notifying.")
            return None
        if config.notification.auth_token is None or config.notification.auth_token == "":
            logger.warning("Twilio auth token not set. Not notifying.")
            return None
        if (config.notification.sending_number is None
                or config.notification.sending_number == ""):
            logger.warning("Twilio SMS sending number not set. Not notifying.")
            return None

        account_sid = config.notification.sid
        auth_token = config.notification.auth_token
        sender = config.notification.sending_number
        return Notifier(account_sid, auth_token, sender)

    def notify(self, phone_number, message_body):
        """Sends an SMS notification to the specified phone number with the specified body"""

        client = Client(self.account_sid, self.auth_token)
        message = client.messages.create(
            body = message_body, from_=self.sending_number, to=phone_number)

        return message.sid
