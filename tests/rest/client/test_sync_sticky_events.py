#
# This file is licensed under the Affero General Public License (AGPL) version 3.
#
# Copyright (C) 2026, Element Creations Ltd.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# See the GNU Affero General Public License for more details:
# <https://www.gnu.org/licenses/agpl-3.0.html>.

import sqlite3

from twisted.internet.testing import MemoryReactor

from synapse.api.constants import EventTypes, EventUnsignedContentFields
from synapse.rest import admin
from synapse.rest.client import login, register, room, sync
from synapse.server import HomeServer
from synapse.types import JsonDict
from synapse.util.clock import Clock
from synapse.util.duration import Duration

from tests import unittest
from tests.utils import USE_POSTGRES_FOR_TESTS


class SyncStickyEventsTestCase(unittest.HomeserverTestCase):
    """
    Tests for oldschool (v3) /sync with sticky events (MSC4354)
    """

    if not USE_POSTGRES_FOR_TESTS and sqlite3.sqlite_version_info < (3, 40, 0):
        # We need the JSON functionality in SQLite
        skip = f"SQLite version is too old to support sticky events: {sqlite3.sqlite_version_info} (See https://github.com/element-hq/synapse/issues/19428)"

    servlets = [
        room.register_servlets,
        login.register_servlets,
        register.register_servlets,
        admin.register_servlets,
        sync.register_servlets,
    ]

    def default_config(self) -> JsonDict:
        config = super().default_config()
        config["experimental_features"] = {"msc4354_enabled": True}
        return config

    def prepare(self, reactor: MemoryReactor, clock: Clock, hs: HomeServer) -> None:
        # Register an account
        self.user_id = self.register_user("user1", "pass")
        self.token = self.login(self.user_id, "pass")

        # Create a room
        self.room_id = self.helper.create_room_as(self.user_id, tok=self.token)

    def test_single_sticky_event_appears_in_initial_sync(self) -> None:
        """
        Test sending a single sticky event and then doing an initial /sync.
        """

        # Send a sticky event
        sticky_event_response = self.helper.send_sticky_event(
            self.room_id,
            EventTypes.Message,
            duration=Duration(minutes=1),
            content={"body": "sticky message", "msgtype": "m.text"},
            tok=self.token,
        )
        sticky_event_id = sticky_event_response["event_id"]

        # Perform initial sync
        channel = self.make_request(
            "GET",
            "/sync",
            access_token=self.token,
        )

        self.assertEqual(channel.code, 200, channel.result)

        # Get timeline events from the sync response
        timeline_events = channel.json_body["rooms"]["join"][self.room_id]["timeline"][
            "events"
        ]

        # Verify the sticky event is present and has the sticky TTL field
        self.assertEqual(
            timeline_events[-1]["event_id"],
            sticky_event_id,
            f"Sticky event {sticky_event_id} not found in sync timeline",
        )
        self.assertEqual(
            timeline_events[-1]["unsigned"][EventUnsignedContentFields.STICKY_TTL],
            59_900,
        )

        self.assertNotIn(
            "sticky",
            channel.json_body["rooms"]["join"][self.room_id],
            "Unexpected sticky section of sync response",
        )

    def test_sticky_event_beyond_timeline_in_initial_sync(self) -> None:
        """
        Test that sends a sticky event into a room and pushes it out of the
        timeline window.
        The test then checks that the event comes down the dedicated sticky
        section of the sync response.
        """
        # Send the first sticky event
        first_sticky_response = self.helper.send_sticky_event(
            self.room_id,
            EventTypes.Message,
            duration=Duration(minutes=1),
            content={"body": "first sticky", "msgtype": "m.text"},
            tok=self.token,
        )
        first_sticky_event_id = first_sticky_response["event_id"]

        # Send 10 regular timeline events,
        # in order to push the sticky event out of the timeline window
        # that the /sync will get.
        regular_event_ids = []
        for i in range(10):
            response = self.helper.send(
                room_id=self.room_id,
                body=f"regular message {i}",
                tok=self.token,
            )
            regular_event_ids.append(response["event_id"])

        # Send another sticky event
        second_sticky_response = self.helper.send_sticky_event(
            self.room_id,
            EventTypes.Message,
            duration=Duration(minutes=1),
            content={"body": "second sticky", "msgtype": "m.text"},
            tok=self.token,
        )
        second_sticky_event_id = second_sticky_response["event_id"]

        # Perform initial sync
        channel = self.make_request(
            "GET",
            "/sync",
            access_token=self.token,
        )
        self.assertEqual(channel.code, 200, channel.result)

        # Get timeline events from the sync response
        timeline_events = channel.json_body["rooms"]["join"][self.room_id]["timeline"][
            "events"
        ]
        sticky_events = channel.json_body["rooms"]["join"][self.room_id][
            "msc4354_sticky"
        ]["events"]

        # Extract event IDs from the timeline
        timeline_event_ids = [event["event_id"] for event in timeline_events]

        # The sticky event is not in the timeline section
        self.assertNotIn(
            first_sticky_event_id,
            timeline_event_ids,
            f"First sticky event {first_sticky_event_id} unexpectedly found in sync timeline",
        )

        # The first 'regular' event is also not in the timeline section
        self.assertNotIn(
            regular_event_ids[0],
            timeline_event_ids,
            f"First regular event {regular_event_ids[0]} unexpectedly found in sync timeline",
        )

        # But the second sticky event *is*
        self.assertEqual(
            timeline_events[-1]["event_id"],
            second_sticky_event_id,
            f"Second sticky event {second_sticky_event_id} not found in sync timeline",
        )
        self.assertEqual(
            timeline_events[-1]["unsigned"][EventUnsignedContentFields.STICKY_TTL],
            59_900,
        )

        # The first sticky event is only found in the sticky section, which doesn't
        # include the second sticky event (as that one was present in the timeline)
        self.assertEqual(
            len(sticky_events),
            1,
            f"Expected exactly 1 item in sticky events section, got {sticky_events}",
        )
        self.assertEqual(sticky_events[0]["event_id"], first_sticky_event_id)
        self.assertEqual(
            sticky_events[0]["unsigned"][EventUnsignedContentFields.STICKY_TTL], 58_800
        )
