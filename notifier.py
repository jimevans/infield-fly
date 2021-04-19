import os
from twilio.rest import Client


class Notifier:
    def __init__(self, account_sid, auth_token, sending_number):
        super().__init__()
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.sending_number = sending_number

    
    @classmethod
    def create_default_notifier(cls):
        if "TWILIO_ACCOUNT_SID" not in os.environ:
            print("TWILIO_ACCOUNT_SID environment variable not set. Not notirying")
            return None
        if "TWILIO_AUTH_TOKEN" not in os.environ:
            print("TWILIO_AUTH_TOKEN environment variable not set. Not notirying")
            return None
        if "TWILIO_SENDING_NUMBER" not in os.environ:
            print("TWILIO_SENDING_NUMBER environment variable not set. Not notirying")
            return None

        account_sid = os.environ["TWILIO_ACCOUNT_SID"]
        auth_token = os.environ["TWILIO_AUTH_TOKEN"]
        sender = os.environ["TWILIO_SENDING_NUMBER"]
        notification_receiver = os.environ["TWILIO_SMS_RECIPIENT"]
        return Notifier(account_sid, auth_token, sender)


    def notify(self, phone_number, message_body):
        client = Client(self.account_sid, self.auth_token)
        message = client.messages.create(body = message_body,
            from_ = self.sending_number, to = phone_number)

        return message.sid
