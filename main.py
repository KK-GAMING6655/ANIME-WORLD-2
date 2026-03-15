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

# Updated table to include card_id
cursor.execute('''CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, balance INTEGER DEFAULT 0)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS cards (
                    card_id INTEGER PRIMARY KEY, 
                    name TEXT UNIQUE, 
                    rarity TEXT, 
                    value INTEGER, 
                    image TEXT)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS rarities (name TEXT PRIMARY KEY, color TEXT)''')
conn.commit()

# --- 3. Pagination View for /view_card ---
class CardPaginator(ui.View):
    def __init__(self, cards, start_index):
        super().__init__(timeout=60)
        self.cards = cards
        self.current_page = start_index

    def create_embed(self):
        card = self.cards[self.current_page]
        # card[0]=id, card[1]=name, card[2]=rarity, card[3]=value, card[4]=image
        embed = discord.Embed(description=f"Page {self.current_page + 1}/{len(self.cards)}", color=discord.Color.gold())
        embed.add_field(name=f"**{card[1]}**", value=f"**Rarity:** {card[2]}\n**Value:** 🪙 {card[3]}\n**Card ID:** `{card[0]}`", inline=False)
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
        print("Slash commands synced!")

client = GachaBot()

@client.event
async def on_ready():
    print(f'Logged in as {client.user}!')

# --- 5. Automatic Economy ---
@client.event
async def on_message(message):
    if message.author.bot: return
    coins = random.randint(1, 5)
    cursor.execute('INSERT INTO users (id, balance) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET balance = balance + ?', (str(message.author.id), coins, coins))
    conn.commit()

# --- 6. Commands ---

@client.tree.command(name="balance", description="Check your coin balance")
async def balance(interaction: discord.Interaction):
    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(interaction.user.id),))
    row = cursor.fetchone()
    await interaction.response.send_message(f"💰 You have **{row[0] if row else 0}** coins!")

@client.tree.command(name="add_card", description="Admin: Add a new card")
async def add_card(interaction: discord.Interaction, name: str, rarity: str, value: int, image_url: str):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild:
        return await interaction.followup.send("❌ Admin only!")

    # Generate a unique 6-digit Card ID
    while True:
        new_id = random.randint(100000, 999999)
        cursor.execute('SELECT 1 FROM cards WHERE card_id = ?', (new_id,))
        if not cursor.fetchone(): break

    cursor.execute('INSERT INTO cards (card_id, name, rarity, value, image) VALUES (?, ?, ?, ?, ?)', 
                   (new_id, name, rarity, value, image_url))
    conn.commit()
    await interaction.followup.send(f"✅ Card **{name}** added with ID: `{new_id}`")

@client.tree.command(name="view_card", description="View a card by name or ID")
@app_commands.describe(query="Enter the Card Name or 6-digit Card ID")
async def view_card(interaction: discord.Interaction, query: str):
    await interaction.response.defer(ephemeral=True)
    
    # Get all cards to enable scrolling
    cursor.execute('SELECT * FROM cards')
    all_cards = cursor.fetchall()
    
    if not all_cards:
        return await interaction.followup.send("❌ There are no cards in the database.")

    # Find the index of the card the user asked for
    found_index = -1
    for i, card in enumerate(all_cards):
        # Check if query matches Name (card[1]) or ID (card[0])
        if query.lower() == str(card[1]).lower() or query == str(card[0]):
            found_index = i
            break
    
    if found_index == -1:
        return await interaction.followup.send(f"❌ Card '{query}' not found.")

    view = CardPaginator(all_cards, found_index)
    await interaction.followup.send(embed=view.create_embed(), view=view)

# --- 7. Run ---
if __name__ == '__main__':
    Thread(target=run_flask).start()
    client.run(os.environ.get('DISCORD_TOKEN'))
        
