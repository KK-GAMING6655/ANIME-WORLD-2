import asyncio
from discord.ext import tasks

# -------------------------------------------------------------
# ⚙️ CONFIGURATION: PASTE YOUR VOICE / TEXT CHANNEL IDs HERE
# -------------------------------------------------------------
SERVERS_CHANNEL_ID = 123456789012345678  # Replace with your Server Count Channel ID
USERS_CHANNEL_ID = 987654321098765432    # Replace with your User Count Channel ID


@tasks.loop(minutes=30)
async def update_status_channels(client, cursor):
    """Background task running every 30 minutes to update channel names."""
    try:
        # 1. Fetch total server count directly from the bot
        server_count = len(client.guilds)

        # 2. Fetch total unique user count from Turso database
        cursor.execute("SELECT COUNT(*) FROM users")
        row = cursor.fetchone()
        user_count = row[0] if row else 0

        # 3. Update the Server Count channel
        servers_channel = client.get_channel(SERVERS_CHANNEL_ID)
        if servers_channel:
            new_server_name = f"🌐 Servers: {server_count}"
            if servers_channel.name != new_server_name:
                await servers_channel.edit(name=new_server_name)

        # 4. Update the User Count channel
        users_channel = client.get_channel(USERS_CHANNEL_ID)
        if users_channel:
            new_user_name = f"👥 Users: {user_count}"
            if users_channel.name != new_user_name:
                await users_channel.edit(name=new_user_name)

        print(f"[Status Update] Successfully updated: {server_count} Servers, {user_count} Users")

    except Exception as e:
        print(f"[Status Update Error] Failed to update channels: {e}")


@update_status_channels.before_loop
async def before_status_update():
    """Wait until the bot is completely logged in before starting the loop."""
    await asyncio.sleep(10)  # Short delay after startup
          
