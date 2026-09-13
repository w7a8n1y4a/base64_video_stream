import time

from pepeunit_client import PepeunitClient, RestartMode
from pepeunit_client.enums import BaseInputTopicType, SearchTopicType, SearchScope

from src.enums import EncoderAction
from src.renderer import Renderer
from src.video_processor import VideoProcessor
from src.streamer import Streamer
from src.navigator import Navigator

_MQTT_RETRY_DELAY_START = 2.0
_MQTT_RETRY_DELAY_MAX = 30.0


def get_version() -> str:
    try:
        with open('pyproject.toml', 'r') as f:
            for line in f:
                if line.strip().startswith('version'):
                    return line.split('=', 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return '?.?.?'


def read_runtime_settings(client: PepeunitClient) -> dict:
    return {
        'fps': getattr(client.settings, 'VIDEO_FPS', 10),
        'ui_fps': getattr(client.settings, 'UI_FPS', 5),
        'width': getattr(client.settings, 'WIDTH', 128),
        'height': getattr(client.settings, 'HEIGHT', 64),
        'items_per_page': getattr(client.settings, 'ITEMS_PER_PAGE', None),
        'seek_seconds': getattr(client.settings, 'SEEK_SECONDS', 5),
    }


def apply_runtime_settings(
    client: PepeunitClient,
    renderer: Renderer,
    video_processor: VideoProcessor,
    navigator: Navigator,
) -> None:
    settings = read_runtime_settings(client)
    fps = settings['fps'] or 10
    ui_fps = settings['ui_fps'] or 5

    client.cycle_speed = 1.0 / fps
    video_processor.apply_settings(settings['width'], settings['height'], fps)
    renderer.reconfigure(settings['width'], settings['height'], settings['items_per_page'])
    navigator.apply_settings(settings['seek_seconds'], ui_fps)


def connect_mqtt(client: PepeunitClient) -> None:
    mqtt = client.mqtt_client
    delay = _MQTT_RETRY_DELAY_START
    attempt = 1
    while True:
        paho = getattr(mqtt, '_client', None)
        if paho is not None and paho.is_connected():
            return

        try:
            mqtt.connect()
            return
        except Exception as e:
            try:
                mqtt.disconnect()
            except Exception:
                pass
            mqtt._client = None
            client.logger.warning(
                f'MQTT connect failed (attempt {attempt}): {e}, retry in {delay:.0f}s'
            )
            time.sleep(delay)
            delay = min(delay * 2, _MQTT_RETRY_DELAY_MAX)
            attempt += 1


def main() -> None:
    client = PepeunitClient(
        env_file_path='env.json',
        schema_file_path='schema.json',
        log_file_path='log.json',
        enable_mqtt=True,
        enable_rest=True,
        restart_mode=RestartMode.RESTART_EXEC,
    )

    settings = read_runtime_settings(client)
    client.cycle_speed = 1.0 / (settings['fps'] or 10)
    _last_applied_fps = settings['fps']

    version = get_version()

    renderer = Renderer(
        settings['width'],
        settings['height'],
        items_per_page=settings['items_per_page'],
    )
    streamer = Streamer(client)
    streamer.set_renderer(renderer)

    video_processor = VideoProcessor(
        videos_dir='videos',
        frames_dir='frames',
        client=client,
        width=settings['width'],
        height=settings['height'],
        target_fps=settings['fps'],
    )

    navigator = Navigator(
        video_processor=video_processor,
        renderer=renderer,
        version=version,
        seek_seconds=settings['seek_seconds'],
        ui_fps=settings['ui_fps'],
    )

    client.logger.info('Synchronizing video library state...')
    video_processor.scan_and_sync()
    client.logger.info('Video library synchronized')

    def on_input(client_ref: PepeunitClient, msg) -> None:
        try:
            env_topics = client_ref.schema.input_base_topic.get(
                BaseInputTopicType.ENV_UPDATE_PEPEUNIT.value, []
            )
            if msg.topic in env_topics:
                apply_runtime_settings(client_ref, renderer, video_processor, navigator)
                return

            topic_parts = msg.topic.split('/')
            if len(topic_parts) == 3:
                topic_name = client_ref.schema.find_topic_by_unit_node(
                    msg.topic, SearchTopicType.FULL_NAME, SearchScope.INPUT
                )
                if topic_name == 'encoder_action/pepeunit':
                    action = EncoderAction(msg.payload.strip())
                    navigator.handle_action(action)
        except ValueError:
            client_ref.logger.warning(f'Unknown encoder action: {msg.payload}')
        except Exception as e:
            client_ref.logger.error(f'Input handler error: {e}')

    def on_output(client_ref: PepeunitClient) -> None:
        nonlocal _last_applied_fps
        current_fps = video_processor.target_fps
        if current_fps != _last_applied_fps:
            client_ref.cycle_speed = 1.0 / current_fps
            _last_applied_fps = current_fps

        frame = navigator.get_next_frame()
        if frame:
            streamer.send_frame(frame)

    client.set_mqtt_input_handler(on_input)
    client.set_output_handler(on_output)

    video_processor.start_background()
    client.logger.info('Background video processor started')

    connect_mqtt(client)
    client.subscribe_all_schema_topics()

    client.logger.info(f'Video Stream v{version} running at {settings["fps"]} FPS')

    try:
        client.run_main_cycle()
    finally:
        video_processor.stop()
        client.mqtt_client.disconnect()
        client.logger.info('Shutdown complete')


if __name__ == '__main__':
    main()
