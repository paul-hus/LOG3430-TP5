from locust import task, between, TaskSet, User, HttpUser
from client import SocialMediaClient

class ExampleTaskSet(TaskSet):
    def __init__(self, user: User):
        super().__init__(user)
        self.social_media_client = SocialMediaClient(self.client)

    def on_start(self):
        self.social_media_client.login()

    @task(35)
    def view_feed_api(self):
        self.social_media_client.view_feed()

    @task(23)
    def like_post_api(self):
        self.social_media_client.like_post()

    @task(22)
    def view_profile_api(self):
        self.social_media_client.view_profile()

    @task(15)
    def follow_user_api(self):
        self.social_media_client.follow_user()

    @task(5)
    def create_post_api(self):
        self.social_media_client.create_post()

class ExampleSocialMediaUser(HttpUser):
    wait_time = between(1, 5)
    tasks = [ExampleTaskSet]
