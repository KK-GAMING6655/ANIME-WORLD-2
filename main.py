import discord
from discord import app_commands, ui
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

# Initialize all tables
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
cursor.execute('''CREATE TABLE IF NOT EXISTS rarities (name TEXT PRIMARY KEY, color TEXT)''')
conn.commit()

# --- 3. Premium Pagination for /card_list ---
class CardPaginator(ui.View):
    def __init__(self, cards, start_index):
        super().__init__(timeout=60)
        self.cards = cards
        self.current_page = start_index

    def create_embed(self):
        card = self.cards[self.current_page]
        # card[0]=id, card[1]=name, card[2]=rarity, card[3]=value, card[4]=image
        
        # Calculate owners
        cursor.execute('SELECT COUNT(DISTINCT user_id) FROM inventory WHERE card_id = ?', (card[0],))
        owners_count = cursor.fetchone()[0]

        embed = discord.Embed(description=f"**Page {self.current_page + 1}/{len(self.cards)}**", color=discord.Color.blue())
        embed.add_field(
            name=f"**{card[1]}**", 
            value=f"**Rarity:** {card[2]}\n**Value:** {card[3]} 🪙\n**Card ID:** `{card[0]}`\n**Owners:** {owners_count} 👥", 
            inline=False
        )
        embed.set_image(url=card[4])
        return embed

    @ui.button(label="⬅️", style=discord.ButtonStyle.grey)
    async def previous_page(self, interaction: discord.Interaction, button: ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            await interaction.response.edit_message(embed=self.create_embed(), view=self)
        else:
            await interaction.response.defer()

    @ui.button(label="➡️", style=discord.ButtonStyle.grey)
    async def next_page(self, interaction: discord.Interaction, button: ui.Button):
        if self.current_page < len(self.cards) - 1:
            self.current_page += 1
            await interaction.response.edit_message(embed=self.create_embed(), view=self)
        else:
            await interaction.response.defer()

# --- 4. Discord Bot Setup ---
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

# --- 5. Economy: Coins for Messages ---
@client.event
async def on_message(message):
    if message.author.bot: return
    coins_earned = random.randint(1, 5)
    cursor.execute('INSERT INTO users (id, balance) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET balance = balance + ?', 
                   (str(message.author.id), coins_earned, coins_earned))
    conn.commit()

# --- 6. Commands (Parts 1 & 2 Complete) ---

@client.tree.command(name="balance", description="Check your coin balance")
async def balance(interaction: discord.Interaction):
    await interaction.response.defer()
    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(interaction.user.id),))
    row = cursor.fetchone()
    bal = row[0] if row else 0
    await interaction.followup.send(f"💰 You have **{bal}** coins!")

@client.tree.command(name="addcoin", description="Admin: Add coins to a user")
async def addcoin(interaction: discord.Interaction, user: discord.Member, amount: int):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.followup.send("❌ Admin only!")
    cursor.execute('INSERT INTO users (id, balance) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET balance = balance + ?', 
                   (str(user.id), amount, amount))
    conn.commit()
    await interaction.followup.send(f"✅ Added **{amount}** coins to {user.mention}!")

@client.tree.command(name="add_card", description="Admin: Add a new card")
async def add_card(interaction: discord.Interaction, name: str, rarity: str, value: int, image_url: str):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.followup.send("❌ Admin only!")
    
    # Generate Unique 6-digit ID
    while True:
        new_id = random.randint(100000, 999999)
        cursor.execute('SELECT 1 FROM cards WHERE card_id = ?', (new_id,))
        if not cursor.fetchone(): break

    cursor.execute('INSERT INTO cards (card_id, name, rarity, value, image) VALUES (?, ?, ?, ?, ?)', 
                   (new_id, name, rarity, value, image_url))
    conn.commit()
    await interaction.followup.send(f"✅ Card **{name}** added with ID: `{new_id}`")

@client.tree.command(name="card_list", description="Admin: See all registered cards")
async def card_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.followup.send("❌ Admin only!")

    cursor.execute('SELECT * FROM cards')
    all_cards = cursor.fetchall()
    if not all_cards:
        return await interaction.followup.send("❌ Database is empty.")

    view = CardPaginator(all_cards, 0)
    await interaction.followup.send(embed=view.create_embed(), view=view)

@client.tree.command(name="view_card", description="View a card by name or ID")
async def view_card(interaction: discord.Interaction, query: str):
    await interaction.response.defer(ephemeral=True)
    
    cursor.execute('SELECT * FROM cards WHERE name = ? OR card_id = ?', (query, query))
    card = cursor.fetchone()
    if not card:
        return await interaction.followup.send(f"❌ Card '{query}' not found.")

    cursor.execute('SELECT COUNT(DISTINCT user_id) FROM inventory WHERE card_id = ?', (card[0],))
    owners_count = cursor.fetchone()[0]

    embed = discord.Embed(color=discord.Color.blue())
    embed.add_field(
        name=f"**{card[1]}**", 
        value=f"**Rarity:** {card[2]}\n**Value:** {card[3]} 🪙\n**Card ID:** `{card[0]}`\n**Owners:** {owners_count} 👥", 
        inline=False
    )
    embed.set_image(url=card[4])
    await interaction.followup.send(embed=embed)

# --- 7. Run Bot ---
if __name__ == '__main__':
    Thread(target=run_flask).start()
    token = os.environ.get('DISCORD_TOKEN')
    if token:
        client.run(token)
    else:
        print("No DISCORD_TOKEN found!")
        
