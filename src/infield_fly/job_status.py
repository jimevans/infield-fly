"""Module containing the status values of a job, importable without the job dependencies"""

from enum import Enum


class JobStatus(Enum):
    """Provides the enumerated values for the status of a job"""

    WAITING = "waiting"
    SEARCHING = "searching"
    ADDING = "adding"
    DOWNLOADING = "downloading"
    PENDING = "pending"
    CONVERTING = "converting"
    COMPLETED = "completed"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value):
        """Returns the UNKNOWN value for values not matching valid values"""

        return cls(cls.UNKNOWN)
