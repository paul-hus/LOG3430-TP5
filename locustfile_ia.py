"""Markov-chain load test inferred from the per-user request order in logs.txt."""

import random
from fractions import Fraction

from locust import HttpUser, TaskSet, User, between, task

from client import SocialMediaClient


def _probabilities(counts):
    """Convert observed transition counts to exact probabilities."""
    total = sum(counts.values())
    return {state: Fraction(count, total) for state, count in counts.items()}


# States use SocialMediaClient method names. Each row contains
# P(next state | current state); transitions never observed have probability 0.
# The rows below account for all 1,002 transitions between consecutive requests
# by the same authenticated user. Anonymous logins occur only during startup.
TRANSITION_PROBABILITIES = {
    # Denominator: 349 outgoing requests from view_feed.
    "view_feed": _probabilities({
        "view_feed": 0,
        "create_post": 52,
        "view_profile": 62,
        "like_post": 235,
        "follow_user": 0,
    }),
    # Denominator: 52 outgoing requests from create_post.
    "create_post": _probabilities({
        "view_feed": 52,
        "create_post": 0,
        "view_profile": 0,
        "like_post": 0,
        "follow_user": 0,
    }),
    # Denominator: 219 outgoing requests from view_profile.
    "view_profile": _probabilities({
        "view_feed": 70,
        "create_post": 0,
        "view_profile": 0,
        "like_post": 0,
        "follow_user": 149,
    }),
    # Denominator: 234 outgoing requests from like_post.
    "like_post": _probabilities({
        "view_feed": 73,
        "create_post": 0,
        "view_profile": 161,
        "like_post": 0,
        "follow_user": 0,
    }),
    # Denominator: 148 outgoing requests from follow_user.
    "follow_user": _probabilities({
        "view_feed": 148,
        "create_post": 0,
        "view_profile": 0,
        "like_post": 0,
        "follow_user": 0,
    }),
}

# Every authenticated trace starts with GET /feed immediately after POST /login.
INITIAL_STATE = "view_feed"

# Exact rational arithmetic makes every row sum to exactly one.
assert all(sum(row.values(), Fraction(0)) == 1
           for row in TRANSITION_PROBABILITIES.values())


class MarkovTaskSet(TaskSet):
    def __init__(self, user: User):
        super().__init__(user)
        self.social_media_client = SocialMediaClient(self.client)
        self._state = INITIAL_STATE

    def on_start(self):
        self.social_media_client.login()
        self._state = INITIAL_STATE

    @task
    def advance_markov_chain(self):
        # Reusing SocialMediaClient preserves every URL, payload, parameter range,
        # authorization header, and Locust request name from the existing test.
        getattr(self.social_media_client, self._state)()

        transitions = TRANSITION_PROBABILITIES[self._state]
        self._state = random.choices(
            population=tuple(transitions),
            weights=tuple(transitions.values()),
            k=1,
        )[0]


class MarkovSocialMediaUser(HttpUser):
    # logs.txt contains within-user gaps from 1 through 5 seconds inclusive.
    wait_time = between(1, 5)
    tasks = [MarkovTaskSet]
