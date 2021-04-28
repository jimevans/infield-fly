import os
from twilio.rest import Client


class Notifier:

    def __init__(self, account_sid, auth_token, sending_number):

        super().__init__()
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.sending_number = sending_number
  
    @classmethod
    def create_default_notifier(cls, notification_settings):
        if notification_settings.sid is None or notification_settings.sid == "":
            print("Twilio account SID not set. Not notifying.")
            return None
        if notification_settings.auth_token is None or notification_settings.auth_token == "":
            print("Twilio auth token not set. Not notifying.")
            return None
        if notification_settings.sending_number is None or notification_settings.sending_number == "":
            print("Twilio SMS sending number not set. Not notifying.")
            return None

        account_sid = notification_settings.sid
        auth_token = notification_settings.auth_token
        sender = notification_settings.sending_number
        return Notifier(account_sid, auth_token, sender)

    def notify(self, phone_number, message_body):
        client = Client(self.account_sid, self.auth_token)
        message = client.messages.create(body = message_body,
            from_ = self.sending_number, to = phone_number)

        return message.sid
