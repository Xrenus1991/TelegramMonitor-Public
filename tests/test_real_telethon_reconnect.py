"""Real Telethon TCP reconnect against a disposable loopback peer, never Telegram."""
import asyncio,collections,importlib.metadata,logging,os
import pytest
def test_real_telethon_sender_reconnects_after_tcp_close():
    try:importlib.metadata.version("Telethon")
    except importlib.metadata.PackageNotFoundError:pytest.skip("actual Telethon required")
    from telethon.crypto import AuthKey
    from telethon.network.mtprotosender import MTProtoSender
    from telethon.network.connection.tcpfull import ConnectionTcpFull
    async def scenario():
        accepted=0;writers=set();tasks=set();recovered=asyncio.Event()
        async def peer(reader,writer):
            nonlocal accepted
            accepted+=1;number=accepted;writers.add(writer)
            task=asyncio.current_task();tasks.add(task)
            try:
                if number==1:
                    await asyncio.sleep(0.1)
                else:
                    await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()
                writers.discard(writer);tasks.discard(task)
        async def on_reconnect():recovered.set()
        server=await asyncio.start_server(peer,"127.0.0.1",0)
        port=server.sockets[0].getsockname()[1]
        logs=collections.defaultdict(lambda:logging.getLogger("synthetic-telethon-transport"))
        # Random local-only AuthKey skips external key exchange; no account or session.
        sender=MTProtoSender(AuthKey(os.urandom(256)),loggers=logs,retries=3,delay=0.05,
                             auto_reconnect=True,auto_reconnect_callback=on_reconnect)
        try:
            await sender.connect(ConnectionTcpFull("127.0.0.1",port,1,loggers=logs))
            await asyncio.wait_for(recovered.wait(),timeout=15)
            assert accepted>=2 and sender.is_connected()
        finally:
            await sender.disconnect()
            assert not sender.is_connected()
            server.close();await server.wait_closed()
            for writer in list(writers):writer.close()
            if tasks:await asyncio.wait_for(asyncio.gather(*list(tasks),return_exceptions=True),5)
    asyncio.run(scenario())
