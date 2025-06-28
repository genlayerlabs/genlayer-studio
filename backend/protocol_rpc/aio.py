import asyncio
import concurrent.futures

MAIN_SERVER_LOOP = asyncio.new_event_loop()
MAIN_LOOP_EXITING = MAIN_SERVER_LOOP.create_future()

MAIN_LOOP_DONE = concurrent.futures.Future()


async def run_in_main_server_loop(initial_result):
    my_loop = asyncio.get_event_loop()
    my_target = my_loop.create_future()

    def wait_target():
        async def body():
            try:
                res = await initial_result
            except Exception as e:
                my_loop.call_soon_threadsafe(my_target.set_exception, e)
            else:
                my_loop.call_soon_threadsafe(my_target.set_result, res)

        MAIN_SERVER_LOOP.create_task(body())

    MAIN_SERVER_LOOP.call_soon_threadsafe(wait_target)
    return await my_target
