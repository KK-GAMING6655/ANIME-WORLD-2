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
conn = sqlite3.connect('gacha.db', check_same_thread=False)
cursor = conn.cursor()

# Creating all tables immediately to prevent missing table errors
cursor.execute('''CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, balance INTEGER DEFAULT 0)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS cards (name TEXT PRIMARY KEY, rarity TEXT, value INTEGER, image TEXT)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS rarities (name TEXT PRIMARY KEY, color TEXT)''')
conn.commit()

# --- 3. Discord Bot Setup ---
class GachaBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        print("Slash commands synced successfully!")

client = GachaBot()

@client.event
async def on_ready():
    print(f'Logged in as {client.user}!')

# --- 4. Economy: Coins for Messages ---
@client.event
async def on_message(message):
    if message.author.bot:
        return

    # Give 1 to 5 random coins for every message
    coins_earned = random.randint(1, 5)
    
    cursor.execute('''INSERT INTO users (id, balance) VALUES (?, ?) 
                      ON CONFLICT(id) DO UPDATE SET balance = balance + ?''', 
                   (str(message.author.id), coins_earned, coins_earned))
    conn.commit()

# --- 5. Part 1 Commands (Economy) ---
@client.tree.command(name="balance", description="Check your coin balance")
async def balance(interaction: discord.Interaction):
    await interaction.response.defer() # Tells Discord we are thinking
    
    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(interaction.user.id),))
    row = cursor.fetchone()
    bal = row[0] if row else 0
    
    await interaction.followup.send(f"💰 You have **{bal}** coins!")

@client.tree.command(name="addcoin", description="Admin: Add coins to a user")
@app_commands.describe(user="The user to give coins to", amount="Amount of coins")
async def addcoin(interaction: discord.Interaction, user: discord.Member, amount: int):
    await interaction.response.defer(ephemeral=True)
    
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.followup.send("❌ You don't have permission to do this.")
        return

    cursor.execute('''INSERT INTO users (id, balance) VALUES (?, ?) 
                      ON CONFLICT(id) DO UPDATE SET balance = balance + ?''', 
                   (str(user.id), amount, amount))
    conn.commit()
    
    await interaction.followup.send(f"✅ Added **{amount}** coins to {user.mention}'s balance!")

# --- 6. Part 2 Commands (Card Management) ---
@client.tree.command(name="add_card", description="Admin: Add a new card to the system")
@app_commands.describe(name="Name of the card", rarity="Rarity (e.g. Legendary)", value="Coin value", image_url="Link to the card image")
async def add_card(interaction: discord.Interaction, name: str, rarity: str, value: int, image_url: str):
    await interaction.response.defer(ephemeral=True)
    
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.followup.send("❌ Admin only!")
        return

    try:
        cursor.execute('''INSERT INTO cards (name, rarity, value, image) VALUES (?, ?, ?, ?)
                          ON CONFLICT(name) DO UPDATE SET rarity=excluded.rarity, value=excluded.value, image=excluded.image''', 
                       (name, rarity, value, image_url))
        conn.commit()
        await interaction.followup.send(f"✅ Card **{name}** ({rarity}) has been added/updated!")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@client.tree.command(name="card_list", description="Admin: See all cards registered in the bot")
async def card_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.followup.send("❌ Admin only!")
        return

    cursor.execute('SELECT name, rarity, value FROM cards')
    rows = cursor.fetchall()
    
    if not rows:
        await interaction.followup.send("The card database is currently empty.")
        return

    list_text = "\n".join([f"• **{r[0]}** - {r[1]} (🪙 {r[2]})" for r in rows])
    embed = discord.Embed(title="🗃️ Registered Cards", description=list_text, color=discord.Color.blue())
    await interaction.followup.send(embed=embed)

@client.tree.command(name="view_card", description="View details of a specific card")
@app_commands.describe(name="The exact name of the card")
async def view_card(interaction: discord.Interaction, name: str):
    await interaction.response.defer(ephemeral=True) # Makes it so only the sender sees it
    
    cursor.execute('SELECT * FROM cards WHERE name = ?', (name,))
    card = cursor.fetchone()

    if not card:
        await interaction.followup.send(f"❌ Card '{name}' not found.")
        return

    embed = discord.Embed(title=card[0], color=discord.Color.blue())
    embed.add_field(name="Rarity", value=card[1], inline=True)
    embed.add_field(name="Value", value=f"🪙 {card[2]}", inline=True)
    embed.set_image(url=card[3])
    
    await interaction.followup.send(embed=embed)

# --- 7. Run Everything ---
if __name__ == '__main__':
    Thread(target=run_flask).start()
    
    token = os.environ.get('DISCORD_TOKEN')
    if token:
        client.run(token)
    else:
        print("No DISCORD_TOKEN found in environment variables!")
