import logging

from locust import LoadTestShape, events
from locust.runners import LocalRunner, MasterRunner

from cwm_cdn_api.load_tests import config
from cwm_cdn_api.load_tests.state import get_state
from cwm_cdn_api.load_tests.users.bypass_writer import BypassWriter
from cwm_cdn_api.load_tests.users.cache_getter import CacheGetter
from cwm_cdn_api.load_tests.users.cache_miss_getter import CacheMissGetter


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    state = get_state()
    if isinstance(environment.runner, (MasterRunner, LocalRunner)):
        state.initialize(create_tenants=True)
    else:
        state.initialize(create_tenants=False)


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    if isinstance(environment.runner, (MasterRunner, LocalRunner)):
        logging.info("CDN load test teardown starting")
        get_state().teardown()
        logging.info("CDN load test teardown complete")


class CwmCdnLoadTestShape(LoadTestShape):
    use_common_options = True

    def get_common_options(self):
        options = self.runner.environment.parsed_options
        num_users = getattr(options, "users", None)
        if num_users is None:
            num_users = getattr(options, "num_users", None)
        spawn_rate = getattr(options, "spawn_rate", None)
        if num_users is None or spawn_rate is None:
            raise RuntimeError("Locust common options are missing users or spawn_rate")
        return num_users, spawn_rate

    def tick(self):
        run_time = getattr(self.runner.environment.parsed_options, "run_time", None)
        if run_time and self.get_run_time() >= run_time:
            return None
        user_classes = []
        if config.CWM_CDN_CACHE_GETTER_ENABLED:
            user_classes.append(CacheGetter)
        if config.CWM_CDN_CACHE_MISS_GETTER_ENABLED:
            user_classes.append(CacheMissGetter)
        if config.CWM_CDN_BYPASS_WRITER_ENABLED:
            user_classes.append(BypassWriter)
        if not user_classes:
            raise RuntimeError("No CDN load-test user classes are enabled")
        num_users, spawn_rate = self.get_common_options()
        return (
            num_users,
            spawn_rate,
            user_classes,
        )
