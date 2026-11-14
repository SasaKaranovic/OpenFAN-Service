"""Example sensor plugin.

Copy this file into your config folder's ``plugins/`` directory (``/config/plugins``
inside the container) and restart the container. A new type "Example: random"
appears under Sensors → Add sensor source.

``read()`` runs in a worker thread every poll interval, so plain blocking code is
fine. Return ``{sensor_id: Reading(value)}``. Raise an exception if the whole
source failed: its sensors are then marked as errors and, once the stale timeout
passes, fans using them go to fail-safe.
"""

import random

from openfan_service.sensors import Option, Reading, SensorPlugin


class RandomSensor(SensorPlugin):
    type_name = "example_random"
    title = "Example: random"
    description = "Reports random temperatures. A template for your own plugin."
    default_poll_interval = 5.0
    options = [
        Option("low", "Lowest value", type="number", default=30),
        Option("high", "Highest value", type="number", default=45),
    ]

    def read(self):
        low, high = self.opts["low"], self.opts["high"]
        return {
            "random1": Reading(round(random.uniform(low, high), 1), label="Random 1"),
            "random2": Reading(round(random.uniform(low, high), 1), label="Random 2"),
        }
