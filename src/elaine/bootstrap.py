from .application import Application
from .core import SystemClock
from .db import Database
from .finance import Finance
from .identity import Identity
from .memory import Memory
from .provider import Provider
from .reminders import Reminders


def build(settings, clock=None, provider=None):
    clock = clock or SystemClock()
    db = Database(settings.database)
    identity = Identity(db, settings.timezone)
    app = Application(
        identity,
        Memory(db, identity, clock),
        Finance(db, clock),
        Reminders(db, clock, settings.alarm_interval, settings.alarm_max),
        provider or Provider(settings),
        clock,
        settings,
    )
    return app, db
