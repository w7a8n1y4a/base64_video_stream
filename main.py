import re

from pepeunit_client import PepeunitClient, RestartMode
from pepeunit_client.enums import SearchTopicType, SearchScope

from src.enums import EncoderAction
from src.renderer import Renderer
from src.video_processor import VideoProcessor
from src.streamer import Streamer
from src.navigator import Navigator


def get_version() -> str:
    try:
        with open('pyproject.toml', 'r') as f:
            for line in f:
                if line.strip().startswith('version'):
                    return line.split('=', 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return '?.?.?'


def main() -> None:
    client = PepeunitClient(
        env_file_path='env.json',
        schema_file_path='schema.json',
        log_file_path='log.json',
        enable_mqtt=True,
        enable_rest=True,
        restart_mode=RestartMode.RESTART_EXEC,
    )

    fps = getattr(client.settings, 'VIDEO_FPS', 10)
    ui_fps = getattr(client.settings, 'UI_FPS', 5)
    width = getattr(client.settings, 'WIDTH', 128)
    height = getattr(client.settings, 'HEIGHT', 64)
    items_per_page = getattr(client.settings, 'ITEMS_PER_PAGE', None)
    seek_seconds = getattr(client.settings, 'SEEK_SECONDS', 5)
    client.cycle_speed = 1.0 / fps
    _last_applied_fps = fps

    version = get_version()

    renderer = Renderer(width, height, items_per_page=items_per_page)
    streamer = Streamer(client)
    streamer.set_renderer(renderer)

    video_processor = VideoProcessor(
        videos_dir='videos',
        frames_dir='frames',
        client=client,
        width=width,
        height=height,
        target_fps=fps,
    )

    navigator = Navigator(
        video_processor=video_processor,
        renderer=renderer,
        version=version,
        seek_seconds=seek_seconds,
        ui_fps=ui_fps,
    )

    client.logger.info('Synchronizing video library state...')
    video_processor.scan_and_sync()
    client.logger.info('Video library synchronized')

    def on_input(client_ref: PepeunitClient, msg) -> None:
        try:
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

    client.mqtt_client.connect()
    client.subscribe_all_schema_topics()

    client.logger.info(f'Video Stream v{version} running at {fps} FPS')

    try:
        client.run_main_cycle()
    finally:
        video_processor.stop()
        client.mqtt_client.disconnect()
        client.logger.info('Shutdown complete')


if __name__ == '__main__':
    main()
