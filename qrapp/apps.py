from django.apps import AppConfig
from django.db.backends.signals import connection_created


def _tune_mysql_session(sender, connection, **kwargs):
    """Tune each MariaDB connection for the scan hot path.

    Django's mysql backend sends OPTIONS['init_command'] as ONE statement, so
    extra tuning has to happen here. MariaDB's default
    innodb_lock_wait_timeout is 50s; save_scan only holds a per-student row
    lock for a few milliseconds, so waiting 50s means the scan is already
    lost (two people scanned the same student at once). Failing after 5s
    returns the "scanner busy, scan again" JSON instead of leaving queues of
    scanner devices hanging on a dead lock.
    """
    if connection.vendor != "mysql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION innodb_lock_wait_timeout=5")


class QrappConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'qrapp'

    def ready(self):
        connection_created.connect(_tune_mysql_session, dispatch_uid="qrapp-mysql-tune")

