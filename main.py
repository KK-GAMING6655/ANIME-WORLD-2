import discord
from discord import app_commands, ui
import sqlite3
import os
import random
from flask import Flask
from threading import Thread

# --- 1. WEB SERVER (For UptimeRobot) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is awake and running!"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# --- 2. DATABASE SETUP & SEEDING ---
conn = sqlite3.connect('gacha.db', check_same_thread=False)
cursor = conn.cursor()

def init_db():
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, balance INTEGER DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS cards (
                        card_id INTEGER PRIMARY KEY, 
                        name TEXT UNIQUE, 
                        rarity TEXT, 
                        value INTEGER, 
                        image TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS inventory (
                        user_id TEXT, 
                        card_id INTEGER, 
                        quantity INTEGER DEFAULT 1)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS rarities (
                        name TEXT PRIMARY KEY, 
                        color TEXT, 
                        chance REAL)''')
    
    # Auto-seed the rarities you requested
    default_rarities = [
        ("Common", "808080", 50.0),
        ("Uncommon", "008000", 20.0),
        ("Rare", "0000FF", 10.0),
        ("Epic", "EE82EE", 5.0),
        ("Legendary", "FFFF00", 2.0),
        ("Super Legendary", "FF0000", 1.0)
    ]
    for name, color, chance in default_rarities:
        cursor.execute('INSERT OR IGNORE INTO rarities (name, color, chance) VALUES (?, ?, ?)', (name, color, chance))
    
    conn.commit()

init_db()

# --- 3. UI HELPERS (Paginators) ---

class CardPaginator(ui.View):
    def __init__(self, cards, start_index, title_prefix="Card List"):
        super().__init__(timeout=60)
        self.cards = cards
        self.current_page = start_index
        self.title_prefix = title_prefix

    def get_color(self, rarity_name):
        cursor.execute('SELECT color FROM rarities WHERE name = ?', (rarity_name,))
        res = cursor.fetchone()
        return int(res[0], 16) if res else 0x3498db

    def create_embed(self):
        card = self.cards[self.current_page]
        # Depending on query, card might be (id, name, rarity, value, image) or (id, name, rarity, value, image, quantity)
        card_id, name, rarity, value, image = card[0], card[1], card[2], card[3], card[4]
        quantity = card[5] if len(card) > 5 else None

        cursor.execute('SELECT COUNT(DISTINCT user_id) FROM inventory WHERE card_id = ?', (card_id,))
        owners = cursor.fetchone()[0]

        embed = discord.Embed(title=f"{self.title_prefix}", color=self.get_color(rarity))
        embed.description = f"**Page {self.current_page + 1}/{len(self.cards)}**"
        
        info = f"**Rarity:** {rarity}\n**Value:** {value} 🪙\n**Card ID:** `{card_id}`\n**Owners:** {owners} 👥"
        if quantity: info += f"\n**You Own:** x{quantity}"
        
        embed.add_field(name=f"**{name}**", value=info, inline=False)
        embed.set_image(url=image)
        return embed

    @ui.button(label="⬅️", style=discord.ButtonStyle.grey)
    async def prev(self, interaction: discord.Interaction, button: ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            await interaction.response.edit_message(embed=self.create_embed(), view=self)
        else: await interaction.response.defer()

    @ui.button(label="➡️", style=discord.ButtonStyle.grey)
    async def next(self, interaction: discord.Interaction, button: ui.Button):
        if self.current_page < len(self.cards) - 1:
            self.current_page += 1
            await interaction.response.edit_message(embed=self.create_embed(), view=self)
        else: await interaction.response.defer()

# --- 4. BOT SETUP ---
class GachaBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        print("Bot System Online & Synced!")

client = GachaBot()

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')

# --- 5. ECONOMY LOGIC ---
@client.event
async def on_message(message):
    if message.author.bot: return
    coins = random.randint(1, 5)
    cursor.execute('INSERT INTO users (id, balance) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET balance = balance + ?', 
                   (str(message.author.id), coins, coins))
    conn.commit()

# --- 6. COMMANDS (PART 1, 2, & 3) ---

# --- Admin Commands ---

@client.tree.command(name="addcoin", description="Admin: Give coins to a user")
async def addcoin(interaction: discord.Interaction, user: discord.Member, amount: int):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild: return await interaction.followup.send("❌ Admin only!")
    cursor.execute('INSERT INTO users (id, balance) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET balance = balance + ?', (str(user.id), amount, amount))
    conn.commit()
    await interaction.followup.send(f"✅ Added **{amount}** coins to {user.mention}!")

@client.tree.command(name="add_card", description="Admin: Add a new card")
async def add_card(interaction: discord.Interaction, name: str, rarity: str, value: int, image_url: str):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild: return await interaction.followup.send("❌ Admin only!")
    
    while True:
        new_id = random.randint(100000, 999999)
        cursor.execute('SELECT 1 FROM cards WHERE card_id = ?', (new_id,))
        if not cursor.fetchone(): break

    cursor.execute('INSERT INTO cards (card_id, name, rarity, value, image) VALUES (?, ?, ?, ?, ?)', (new_id, name, rarity, value, image_url))
    conn.commit()
    await interaction.followup.send(f"✅ Card **{name}** added! ID: `{new_id}`")

@client.tree.command(name="card_list", description="Admin: View all cards with scrolling")
async def card_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild: return await interaction.followup.send("❌ Admin only!")
    cursor.execute('SELECT * FROM cards')
    cards = cursor.fetchall()
    if not cards: return await interaction.followup.send("Empty database.")
    view = CardPaginator(cards, 0, "Global Card Database")
    await interaction.followup.send(embed=view.create_embed(), view=view)

@client.tree.command(name="inspect_inventory", description="Admin: View another user's collection")
async def inspect_inventory(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild: return await interaction.followup.send("❌ Admin only!")
    cursor.execute('SELECT c.*, i.quantity FROM inventory i JOIN cards c ON i.card_id = c.card_id WHERE i.user_id = ?', (str(user.id),))
    items = cursor.fetchall()
    if not items: return await interaction.followup.send(f"{user.name}'s inventory is empty.")
    view = CardPaginator(items, 0, f"{user.name}'s Collection")
    await interaction.followup.send(embed=view.create_embed(), view=view)

@client.tree.command(name="card_leaderboard", description="Admin: See top collectors")
async def card_leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()
    cursor.execute('SELECT user_id, COUNT(card_id) FROM inventory GROUP BY user_id ORDER BY COUNT(card_id) DESC LIMIT 10')
    rows = cursor.fetchall()
    if not rows: return await interaction.followup.send("No collectors yet.")
    
    lb = ""
    for i, (uid, count) in enumerate(rows, 1):
        user = client.get_user(int(uid)) or await client.fetch_user(int(uid))
        lb += f"{i}. **{user.name}** — {count} unique cards\n"
    
    embed = discord.Embed(title="🏆 Top Collectors", description=lb, color=0x7289da)
    await interaction.followup.send(embed=embed)

# --- Member Commands ---

@client.tree.command(name="balance", description="Check your coins")
async def balance(interaction: discord.Interaction):
    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(interaction.user.id),))
    row = cursor.fetchone()
    await interaction.response.send_message(f"💰 Balance: **{row[0] if row else 0}** coins!")

@client.tree.command(name="view_card", description="View details of a card")
async def view_card(interaction: discord.Interaction, query: str):
    await interaction.response.defer(ephemeral=True)
    cursor.execute('SELECT * FROM cards WHERE name = ? OR card_id = ?', (query, query))
    card = cursor.fetchone()
    if not card: return await interaction.followup.send("Card not found.")
    
    # Use the paginator logic but without buttons for a single view
    view = CardPaginator([card], 0, "Card Details")
    await interaction.followup.send(embed=view.create_embed())

@client.tree.command(name="inventory", description="View your collection")
async def inventory(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    cursor.execute('SELECT c.*, i.quantity FROM inventory i JOIN cards c ON i.card_id = c.card_id WHERE i.user_id = ?', (str(interaction.user.id),))
    items = cursor.fetchall()
    if not items: return await interaction.followup.send("You don't own any cards yet!")
    view = CardPaginator(items, 0, "Your Collection")
    await interaction.followup.send(embed=view.create_embed(), view=view)

@client.tree.command(name="rarity_list", description="View rarity drop chances")
async def rarity_list(interaction: discord.Interaction):
    cursor.execute('SELECT name, chance FROM rarities ORDER BY chance DESC')
    rows = cursor.fetchall()
    desc = "\n".join([f"✨ **{r[0]}**: {r[1]}%" for r in rows])
    await interaction.response.send_message(embed=discord.Embed(title="Rarity Tiers", description=desc, color=0xFFD700), ephemeral=True)

# --- 7. RUN BOT ---
if __name__ == '__main__':
    Thread(target=run_flask).start()
    token = os.environ.get('DISCORD_TOKEN')
    if token: client.run(token)
    else: print("Error: DISCORD_TOKEN missing!")
        
