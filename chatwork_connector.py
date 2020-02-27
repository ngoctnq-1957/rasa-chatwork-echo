from rasa.core.channels.channel import (
    InputChannel,
    OutputChannel,
    UserMessage,
    CollectingOutputChannel,
    QueueOutputChannel
)
from sanic import Blueprint, response
from sanic.request import Request
from sanic.response import HTTPResponse
import rasa
import logging
import json
import asyncio
import re
import requests
from asyncio import Queue, CancelledError
from typing import Text, Dict, Any, Optional, Callable, Awaitable

logger = logging.getLogger(__name__)


class ChatworkOutput(OutputChannel):
    @classmethod
    def name(cls):
        return "chatwork"

    def __init__(self, token_api: Text, room_id: int) -> None:
        self.room_id = room_id
        self.header = {"X-ChatWorkToken": token_api}

    async def send_text_message(
        self, recipient_id: Optional[Text], text: Text, **kwargs: Any
    ) -> None:
        uri = "https://api.chatwork.com/v2/rooms/" + str(self.room_id) + "/messages"
        data = {"body": text}
        req = requests.post(uri, headers=self.header, data=data)


class ChatworkInput(InputChannel):
    @classmethod
    def name(cls) -> Text:
        return "chatwork"

    @classmethod
    def from_credentials(cls, credentials):
        if not credentials:
            cls.raise_missing_credentials_exception()
        return cls(credentials.get("api_token"))

    def __init__(self, api_token: Text) -> None:
        self.api_token = api_token

    @staticmethod
    def _sanitize_user_message(text):
        """
        Remove all tags.
        """
        for regex, replacement in [
            # to messages
            (r"\[[Tt][Oo]:\d+\]", ""),
            # reply messages
            (r"\[[Rr][Pp] aid=[^]]+\]", ""),
            (r"\[Reply aid=[^]]+\]", ""),
        ]:
            text = re.sub(regex, replacement, text)

        return text.strip()

    def blueprint(
        self, on_new_message: Callable[[UserMessage], Awaitable[None]]
    ) -> Blueprint:
        custom_webhook = Blueprint("chatwork_webhook", "chatwork"
        )

        @custom_webhook.route("/", methods=["GET"])
        async def health(request: Request) -> HTTPResponse:
            return response.json({"signature_tag": "o' kawaii koto."})

        @custom_webhook.route("/webhook", methods=["POST"])
        async def receive(request: Request) -> HTTPResponse:
            
            content = request.json["webhook_event"]

            sender_id = content["from_account_id"]
            room_id = content["room_id"]
            message_id = content["message_id"]
            text = content["body"]
            metadata = {
                "sender_id": sender_id,
                "room_id": room_id,
                "message_id": message_id,
                "text": self._sanitize_user_message(text)
            }

            should_use_stream = rasa.utils.endpoints.bool_arg(
                request, "stream", default=False
            )

            if should_use_stream:
                return response.stream(
                    self.stream_response(
                        on_new_message, text, sender_id, room_id, metadata
                    ),
                    content_type="text/event-stream",
                )
            else:
                out_channel = self.get_output_channel(room_id)
                try:
                    await on_new_message(
                        UserMessage(
                            text,
                            out_channel,
                            sender_id,
                            input_channel=room_id,
                            metadata=metadata,
                        )
                    )
                except CancelledError:
                    logger.error(
                        "Message handling timed out for "
                        "user message '{}'.".format(text)
                    )
                except Exception:
                    logger.exception(
                        "An exception occured while handling "
                        "user message '{}'.".format(text)
                    )
                return response.json("alles gut 👌")

        return custom_webhook
    
    def get_output_channel(self, room_id) -> OutputChannel:
        return ChatworkOutput(self.api_token, room_id)
