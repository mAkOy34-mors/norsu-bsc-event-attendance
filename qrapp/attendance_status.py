"""Per-student attendance status, shared by every report surface.

The dashboard cards, the Reports tab, the Students list and the CSV/Excel
export must all classify a student the same way, from their first Time In and
first Time Out inside the selected window:

    COMPLETED   checked in AND checked out
    IN          checked in, still inside
    OUT         checked out with NO check-in on record
    ABSENT      no scan at all

``OUT`` exists because the scanner's Manual Entry can record a check-out for a
student who never checked in (someone who left early, whose QR would not read,
or who was recorded by staff at the gate). Those rows used to be labelled
ABSENT while their Time Out was still printed next to the label -- an "absent"
row carrying a check-out time -- and they were left out of the Attended count.

Every surface used to keep its own copy of this if/elif, which is how they
drifted apart; they now all call ``classify`` here.
"""

COMPLETED = "COMPLETED"
IN = "IN"
OUT = "OUT"
ABSENT = "ABSENT"

# Status filter values sent by the Reports tab and the Export modal.
PRESENT_FILTER = "present"
ABSENT_FILTER = "absent"
IN_FILTER = "in"
OUT_FILTER = "out"            # checked in AND checked out
OUT_ONLY_FILTER = "out_only"  # checked out with no check-in

PRESENT_STATUSES = (COMPLETED, IN, OUT)


def classify(time_in, time_out):
    """Return ``(status, attendance_date)`` for one student's window scans."""
    if time_in and time_out:
        return COMPLETED, time_out.date()
    if time_in:
        return IN, time_in.date()
    if time_out:
        # Check-out only: the student did attend, they just have no Time In.
        return OUT, time_out.date()
    return ABSENT, None


def is_present(status):
    """Only a student with no scan at all is absent."""
    return status in PRESENT_STATUSES


def matches_filter(status, value):
    """Whether ``status`` belongs in a report filtered by ``value``.

    An unrecognised value shows everything, so a stale client can never end up
    exporting an empty file.
    """
    value = (value or "").strip().lower()
    if not value or value == "all":
        return True
    if value == PRESENT_FILTER:
        return is_present(status)
    if value == ABSENT_FILTER:
        return status == ABSENT
    if value == IN_FILTER:
        return status == IN
    if value == OUT_FILTER:
        return status == COMPLETED
    if value == OUT_ONLY_FILTER:
        return status == OUT
    return True
