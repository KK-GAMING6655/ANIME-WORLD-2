import discord
from discord import app_commands
import sqlite3
import os
import random
from flask import Flask
from threading import Thread

# --- 1. Web Server for UptimeRobot ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is awake and running!"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# --- 2. Database Setup ---
# (Note: This local DB will reset on Render's free tier restarts)
conn = sqlite3.connect('gacha.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, balance INTEGER DEFAULT 0)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS cards (name TEXT PRIMARY KEY, rarity TEXT, value INTEGER, image TEXT)''')
conn.commit()

# --- 3. Discord Bot Setup ---
class GachaBot(discord.Client):
    def __init__(self):
        # We need message intents to read messages for the coin system
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # This syncs your slash commands to Discord automatically
        await self.tree.sync()
        print("Slash commands synced!")

client = GachaBot()

@client.event
async def on_ready():
    print(f'Logged in as {client.user}!')

# --- 4. Economy: Coins for Messages ---
@client.event
async def on_message(message):
    if message.author.bot:
        return

    # Give 1 to 5 random coins
    coins_earned = random.randint(1, 5)
    
    cursor.execute('''INSERT INTO users (id, balance) VALUES (?, ?) 
                      ON CONFLICT(id) DO UPDATE SET balance = balance + ?''', 
                   (str(message.author.id), coins_earned, coins_earned))
    conn.commit()

# --- 5. Slash Commands ---
@client.tree.command(name="balance", description="Check your coin balance")
async def balance(interaction: discord.Interaction):
    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(interaction.user.id),))
    row = cursor.fetchone()
    bal = row[0] if row else 0
    await interaction.response.send_message(f"💰 You have **{bal}** coins!")

@client.tree.command(name="addcoin", description="Admin: Add coins to a user")
@app_commands.describe(user="The user to give coins to", amount="Amount of coins")
async def addcoin(interaction: discord.Interaction, user: discord.Member, amount: int):
    # Basic admin check (Requires manage_guild permission)
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ You don't have permission to do this.", ephemeral=True)
        return

    cursor.execute('''INSERT INTO users (id, balance) VALUES (?, ?) 
                      ON CONFLICT(id) DO UPDATE SET balance = balance + ?''', 
                   (str(user.id), amount, amount))
    conn.commit()
    
    await interaction.response.send_message(f"✅ Added **{amount}** coins to {user.mention}'s balance!")
    
# --- COMMAND: /add_card (Admin Only) ---
@client.tree.command(name="add_card", description="Admin: Add a new card to the system")
@app_commands.describe(name="Name of the card", rarity="Rarity (e.g. Legendary)", value="Coin value", image_url="Link to the card image")
async def add_card(interaction: discord.Interaction, name: str, rarity: str, value: int, image_url: str):
    # Admin check
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return

    try:
        cursor.execute('''INSERT INTO cards (name, rarity, value, image) VALUES (?, ?, ?, ?)
                          ON CONFLICT(name) DO UPDATE SET rarity=excluded.rarity, value=excluded.value, image=excluded.image''', 
                       (name, rarity, value, image_url))
        conn.commit()
        await interaction.response.send_message(f"✅ Card **{name}** ({rarity}) has been added/updated!")
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

# --- COMMAND: /card_list (Admin/Owner Only) ---
@client.tree.command(name="card_list", description="Admin: See all cards registered in the bot")
async def card_list(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message("❌ Admin only!", ephemeral=True)
        return

    cursor.execute('SELECT name, rarity, value FROM cards')
    rows = cursor.fetchall()
    
    if not rows:
        await interaction.response.send_message("The card database is currently empty.")
        return

    list_text = "\n".join([f"• **{r[0]}** - {r[1]} (🪙 {r[2]})" for r in rows])
    embed = discord.Embed(title="🗃️ Registered Cards", description=list_text, color=discord.Color.blue())
    await interaction.response.send_message(embed=embed)

# --- COMMAND: /view_card (Members) ---
@client.tree.command(name="view_card", description="View details of a specific card")
@app_commands.describe(name="The exact name of the card")
async def view_card(interaction: discord.Interaction, name: str):
    cursor.execute('SELECT * FROM cards WHERE name = ?', (name,))
    card = cursor.fetchone()

    if not card:
        await interaction.response.send_message(f"❌ Card '{name}' not found.", ephemeral=True)
        return

    # card[0]=name, card[1]=rarity, card[2]=value, card[3]=image
    # We will handle dynamic rarity colors in Part 3, for now using Blue
    embed = discord.Embed(title=card[0], color=discord.Color.blue())
    embed.add_field(name="Rarity", value=card[1], inline=True)
    embed.add_field(name="Value", value=f"🪙 {card[2]}", inline=True)
    embed.set_image(url=card[3])
    
    # "The reply message is only to see by the sender" = ephemeral=True
    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- 6. Run Everything ---
if __name__ == '__main__':
    # Start the web server in a separate thread
    Thread(target=run_flask).start()
    
    # Start the Discord bot
    token = os.environ.get('DISCORD_TOKEN')
    if token:
        client.run(token)
    else:
        print("No DISCORD_TOKEN found in environment variables!")
