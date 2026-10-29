"""Module for adding and tracking downloads using the SABnzbd API"""

import logging
import os

import requests

REQUEST_TIMEOUT_SECONDS = 30


class SabnzbdError(Exception):

    """Raised when a request to the SABnzbd API fails"""


class SabnzbdClient:

    """Client for the API of a SABnzbd instance"""

    def __init__(self, download_settings):
        super().__init__()
        self.settings = download_settings
        self.logger = logging.getLogger()

    @property
    def api_url(self):
        """Gets the URL of the API end point"""

        scheme = "https" if self.settings.sabnzbd_use_ssl else "http"
        return f"{scheme}://{self.settings.sabnzbd_host}:{self.settings.sabnzbd_port}/api"

    def add_nzb(self, nzb_file_path, name):
        """Adds an NZB file to the download queue, returning the ID of the added download"""

        params = {"mode": "addfile", "nzbname": name}
        if self.settings.sabnzbd_category is not None:
            params["cat"] = self.settings.sabnzbd_category
        with open(nzb_file_path, "rb") as nzb_file:
            response = self.call_api(params, files={
                "name": (os.path.basename(nzb_file_path), nzb_file, "application/x-nzb")})

        if not response.get("status") or len(response.get("nzo_ids", [])) == 0:
            raise SabnzbdError(f"NZB file was not added: {response}")
        return response["nzo_ids"][0]

    def get_download_status(self, nzo_id):
        """
        Gets the status and final path of a download. The status is 'Completed' or 'Failed'
        for a finished download, the status of a download that is still in progress, or None
        if SABnzbd has no download with the ID.
        """

        history = self.call_api({"mode": "history", "nzo_ids": nzo_id})
        for slot in history.get("history", {}).get("slots", []):
            if slot.get("nzo_id") == nzo_id:
                return slot.get("status"), slot.get("storage"), slot.get("fail_message")

        queue = self.call_api({"mode": "queue", "nzo_ids": nzo_id})
        for slot in queue.get("queue", {}).get("slots", []):
            if slot.get("nzo_id") == nzo_id:
                return slot.get("status"), None, None

        return None, None, None

    def delete_history_item(self, nzo_id):
        """Deletes a finished download, and its files, from the download history"""

        self.call_api({"mode": "history", "name": "delete", "value": nzo_id, "del_files": 1})

    def call_api(self, params, files=None):
        """Calls the SABnzbd API, returning the JSON response"""

        request_params = {"apikey": self.settings.sabnzbd_api_key, "output": "json", **params}
        self.logger.debug("Calling SABnzbd API with %s", params)
        try:
            if files is None:
                response = requests.get(self.api_url, params=request_params,
                                        timeout=REQUEST_TIMEOUT_SECONDS)
            else:
                response = requests.post(self.api_url, params=request_params, files=files,
                                         timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as ex:
            raise SabnzbdError(f"Request to SABnzbd failed: {ex}") from ex

        if response.status_code != 200:
            raise SabnzbdError(
                f"Received error response from SABnzbd ({response.status_code}): "
                f"{response.text}")
        try:
            response_value = response.json()
        except ValueError as ex:
            raise SabnzbdError(f"Could not parse SABnzbd response: {response.text}") from ex

        # SABnzbd reports errors, such as an invalid API key, with a 200 response.
        if isinstance(response_value, dict) and "error" in response_value:
            raise SabnzbdError(f"SABnzbd returned error: {response_value['error']}")
        return response_value
