import discord
from discord import app_commands, ui
import sqlite3
import os
import random
from flask import Flask
from threading import Thread

# --- 1. WEB SERVER ---
app = Flask(__name__)
@app.route('/')
def home(): return "Bot is awake!"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# --- 2. DATABASE SETUP ---
conn = sqlite3.connect('gacha.db', check_same_thread=False)
cursor = conn.cursor()

def init_db():
    cursor.execute('CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, balance INTEGER DEFAULT 0)')
    cursor.execute('CREATE TABLE IF NOT EXISTS cards (card_id INTEGER PRIMARY KEY, name TEXT UNIQUE, rarity TEXT, value INTEGER, image TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS inventory (user_id TEXT, card_id INTEGER, quantity INTEGER DEFAULT 1)')
    cursor.execute('CREATE TABLE IF NOT EXISTS rarities (name TEXT PRIMARY KEY, color TEXT, chance REAL)')
    
    default_rarities = [
        ("Common", "808080", 50.0), ("Uncommon", "008000", 20.0),
        ("Rare", "0000FF", 10.0), ("Epic", "EE82EE", 5.0),
        ("Legendary", "FFFF00", 2.0), ("Super Legendary", "FF0000", 1.0)
    ]
    for name, color, chance in default_rarities:
        cursor.execute('INSERT OR IGNORE INTO rarities (name, color, chance) VALUES (?, ?, ?)', (name, color, chance))
    conn.commit()

init_db()

# --- 3. UTILITY FUNCTIONS ---

def get_user_stats(user_id):
    """Calculates rarity counts and total points for a user."""
    cursor.execute('''SELECT c.rarity, SUM(i.quantity) FROM inventory i 
                      JOIN cards c ON i.card_id = c.card_id WHERE i.user_id = ? GROUP BY c.rarity''', (str(user_id),))
    rows = cursor.fetchall()
    stats = {"Common": 0, "Uncommon": 0, "Rare": 0, "Epic": 0, "Legendary": 0, "Super Legendary": 0}
    for rarity, count in rows:
        if rarity in stats: stats[rarity] = count
    
    points = (stats["Common"] * 1) + (stats["Uncommon"] * 2) + (stats["Rare"] * 3) + \
             (stats["Epic"] * 4) + (stats["Legendary"] * 8) + (stats["Super Legendary"] * 10)
    return stats, points

def get_all_leaderboard_data():
    """Ranks all users based on points."""
    cursor.execute('SELECT DISTINCT user_id FROM inventory')
    user_ids = [row[0] for row in cursor.fetchall()]
    leaderboard = []
    for uid in user_ids:
        stats, points = get_user_stats(uid)
        leaderboard.append({"id": uid, "stats": stats, "points": points})
    # Sort by points descending
    leaderboard.sort(key=lambda x: x["points"], reverse=True)
    return leaderboard

# --- 4. PREMIUM UI PAGINATORS ---

class CardPaginator(ui.View):
    def __init__(self, cards, start_index, title_prefix="Card"):
        super().__init__(timeout=60)
        self.cards = cards
        self.current_page = start_index
        self.title_prefix = title_prefix

    def create_embed(self):
        card = self.cards[self.current_page]
        card_id, name, rarity, value, image = card[0], card[1], card[2], card[3], card[4]
        
        cursor.execute('SELECT color FROM rarities WHERE name = ?', (rarity,))
        res = cursor.fetchone()
        color = int(res[0], 16) if res else 0x3498db
        
        cursor.execute('SELECT COUNT(DISTINCT user_id) FROM inventory WHERE card_id = ?', (card_id,))
        owners = cursor.fetchone()[0]

        embed = discord.Embed(title=f"{self.title_prefix}", color=color)
        embed.description = f"**Page {self.current_page + 1}/{len(self.cards)}**"
        embed.add_field(name=f"**{name}**", value=f"**Rarity:** {rarity}\n**Value:** {value} 🪙\n**Card ID:** `{card_id}`\n**Owners:** {owners} 👥", inline=False)
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

class UserLeaderboardPaginator(ui.View):
    def __init__(self, data, start_index, client):
        super().__init__(timeout=60)
        self.data = data
        self.current_page = start_index
        self.client = client

    async def create_embed(self):
        user_data = self.data[self.current_page]
        user = self.client.get_user(int(user_data['id'])) or await self.client.fetch_user(int(user_data['id']))
        s = user_data['stats']
        
        embed = discord.Embed(title=f"Page {self.current_page + 1}/{len(self.data)}", color=0xFFFF00)
        embed.add_field(name=f"#{self.current_page + 1} **{user.name}**", value=(
            f"Common: {s['Common']}\nUncommon: {s['Uncommon']}\nRare: {s['Rare']}\n"
            f"Epic: {s['Epic']}\nLegendary: {s['Legendary']}\nSuper Legendary: {s['Super Legendary']}\n"
            f"**Collection Points: {user_data['points']}**"
        ), inline=False)
        return embed

    @ui.button(label="⬅️", style=discord.ButtonStyle.grey)
    async def prev(self, interaction: discord.Interaction, button: ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            await interaction.response.edit_message(embed=await self.create_embed(), view=self)
        else: await interaction.response.defer()

    @ui.button(label="➡️", style=discord.ButtonStyle.grey)
    async def next(self, interaction: discord.Interaction, button: ui.Button):
        if self.current_page < len(self.data) - 1:
            self.current_page += 1
            await interaction.response.edit_message(embed=await self.create_embed(), view=self)
        else: await interaction.response.defer()

# --- 5. BOT SETUP ---
class GachaBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()

client = GachaBot()

@client.event
async def on_message(message):
    if message.author.bot: return
    c = random.randint(1, 5)
    cursor.execute('INSERT INTO users (id, balance) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET balance = balance + ?', (str(message.author.id), c, c))
    conn.commit()

# --- 6. COMMANDS ---

@client.tree.command(name="card_leaderboard", description="View cards ranked by value")
async def card_leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()
    cursor.execute('SELECT * FROM cards ORDER BY value DESC')
    cards = cursor.fetchall()
    if not cards: return await interaction.followup.send("No cards found.")
    view = CardPaginator(cards, 0, "Global Card Ranking")
    await interaction.followup.send(embed=view.create_embed(), view=view)

@client.tree.command(name="user_leaderboard", description="Top 10 Collectors")
async def user_leaderboard(interaction: discord.Interaction):
    await interaction.response.defer()
    data = get_all_leaderboard_data()[:10] # Top 10 only
    if not data: return await interaction.followup.send("No collectors yet.")
    view = UserLeaderboardPaginator(data, 0, client)
    await interaction.followup.send(embed=await view.create_embed(), view=view)

@client.tree.command(name="rank", description="Check your personal rank and points")
async def rank(interaction: discord.Interaction):
    await interaction.response.defer()
    data = get_all_leaderboard_data()
    user_rank = next((i for i, item in enumerate(data) if item["id"] == str(interaction.user.id)), None)
    
    if user_rank is None: return await interaction.followup.send("You don't have any cards yet!")
    
    user_data = data[user_rank]
    s = user_data['stats']
    embed = discord.Embed(title=f"**{interaction.user.name}**", color=0xFFFF00)
    embed.add_field(name="Stats", value=(
        f"Common: {s['Common']}\nUncommon: {s['Uncommon']}\nRare: {s['Rare']}\n"
        f"Epic: {s['Epic']}\nLegendary: {s['Legendary']}\nSuper Legendary: {s['Super Legendary']}\n"
        f"**Collection Points: {user_data['points']}**\n**Rank: #{user_rank + 1}**"
    ), inline=False)
    await interaction.followup.send(embed=embed)

# (All other previously defined commands like /balance, /add_card, /inventory etc. should remain below)
@client.tree.command(name="balance", description="Check balance")
async def balance(interaction: discord.Interaction):
    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(interaction.user.id),))
    row = cursor.fetchone()
    await interaction.response.send_message(f"💰 Balance: **{row[0] if row else 0}** coins!")

@client.tree.command(name="add_card", description="Admin: Add card")
async def add_card(interaction: discord.Interaction, name: str, rarity: str, value: int, image_url: str):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild: return await interaction.followup.send("❌ Admin!")
    new_id = random.randint(100000, 999999)
    cursor.execute('INSERT INTO cards (card_id, name, rarity, value, image) VALUES (?, ?, ?, ?, ?)', (new_id, name, rarity, value, image_url))
    conn.commit()
    await interaction.followup.send(f"✅ Added {name} (ID: {new_id})")

@client.tree.command(name="inventory", description="View your collection")
async def inventory(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    cursor.execute('SELECT c.*, i.quantity FROM inventory i JOIN cards c ON i.card_id = c.card_id WHERE i.user_id = ?', (str(interaction.user.id),))
    items = cursor.fetchall()
    if not items: return await interaction.followup.send("Inventory empty.")
    view = CardPaginator(items, 0, "Your Collection")
    await interaction.followup.send(embed=view.create_embed(), view=view)

if __name__ == '__main__':
    Thread(target=run_flask).start()
    client.run(os.environ.get('DISCORD_TOKEN'))
        
