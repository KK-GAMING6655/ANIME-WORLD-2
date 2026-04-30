import discord
from discord import app_commands, ui
import sqlite3
import os
import random
from flask import Flask
from threading import Thread
import datetime
# --- 1. WEB SERVER ---
app = Flask(__name__)
@app.route('/')
def home(): return "Bot is awake!"

def run_flask():
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 8080)))

# --- 2. DATABASE SETUP ---
# --- SECTION 2: CLOUD DATABASE SETUP (TURSO) ---
import libsql
import os # Import 'os' to read environment variables

# This pulls the secrets from Render safely
TURSO_URL = os.getenv("TURSO_URL")
TURSO_TOKEN = os.getenv("TURSO_TOKEN")

# Connect to the Cloud Database using the variables
conn = libsql.connect(TURSO_URL, auth_token=TURSO_TOKEN)
cursor = conn.cursor()

def init_db():
    
    # 1. Create all tables (Now living in the cloud!)
    cursor.execute('CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, balance INTEGER DEFAULT 0)')
    cursor.execute('CREATE TABLE IF NOT EXISTS cards (card_id TEXT PRIMARY KEY, name TEXT UNIQUE, rarity TEXT, value INTEGER, image TEXT)')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS inventory (
                        user_id TEXT, 
                        card_id TEXT, 
                        quantity INTEGER DEFAULT 1, 
                        UNIQUE(user_id, card_id))''')
    
    cursor.execute('CREATE TABLE IF NOT EXISTS rarities (name TEXT PRIMARY KEY, color TEXT, chance REAL)')
    cursor.execute('CREATE TABLE IF NOT EXISTS market (selling_id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id TEXT, card_id TEXT, price INTEGER, quantity INTEGER)')
    cursor.execute('CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)')

    # 2. AUTO-REPAIR: Ensure columns exist in the cloud
    cursor.execute("PRAGMA table_info(users)")
    existing_columns = [column[1] for column in cursor.fetchall()]
    
    if "account_status" not in existing_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN account_status TEXT DEFAULT 'public'")
    if "last_beg" not in existing_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_beg TIMESTAMP")
    if "last_daily" not in existing_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_daily TIMESTAMP")

    # 3. Default Settings
    cursor.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('gacha_cost', '1000')")
    
    conn.commit()

init_db()
    
    

# --- 3. UTILITY FUNCTIONS ---

RARITY_ORDER = {
    'Common': 1, 
    'Uncommon': 2, 
    'Rare': 3, 
    'Epic': 4, 
    'Legendary': 5, 
    'Super Legendary': 6
}


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

#--- 4. UI CLASSES ---
class CardPaginator(ui.View):
    def __init__(self, cards, start_index, title_prefix="Card"):
        super().__init__(timeout=60)
        self.cards = cards
        self.current_page = start_index
        self.title_prefix = title_prefix

    def create_embed(self):
        card = self.cards[self.current_page]
        # card structure: (id, name, rarity, value, image)
        card_id, name, rarity, value, image = card[0], card[1], card[2], card[3], card[4]
        
        cursor.execute('SELECT color FROM rarities WHERE name = ?', (rarity,))
        res = cursor.fetchone()
        
        # --- THE FIX IS HERE ---
        # It removes the '#' symbol so Discord understands the color!
        try:
            color = int(res[0].replace("#", ""), 16) if res else 0x3498db
        except:
            color = 0x3498db

        embed = discord.Embed(title=f"{self.title_prefix}", color=color)
        
        # ADDED: Clear Page numbering at the top
        embed.description = f"**Page {self.current_page + 1} of {len(self.cards)}**"

        # LOGIC: Show Quantity for inventories, Owners for global lists
        if "Collection" in self.title_prefix or "Inventory" in self.title_prefix:
            qty = card[5] if len(card) > 5 else 1
            info_text = f"**Rarity:** {rarity}\n**Value:** {value} 🪙\n**Quantity:** x{qty}\n**Card ID:** `{card_id}`"
        else:
            cursor.execute('SELECT COUNT(DISTINCT user_id) FROM inventory WHERE card_id = ?', (card_id,))
            owners_count = cursor.fetchone()[0]
            info_text = f"**Rarity:** {rarity}\n**Value:** {value} 🪙\n**Owners:** {owners_count} 👥\n**Card ID:** `{card_id}`"

        embed.add_field(name=f"**{name}**", value=info_text, inline=False)
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
        


class DropView(ui.View):
    def __init__(self, card, quantity):
        super().__init__(timeout=None)
        self.card = card
        self.remaining = quantity

    @ui.button(label="Get", style=discord.ButtonStyle.green)
    async def get_card(self, interaction: discord.Interaction, button: ui.Button):
        # Initialize the list of users who claimed if it doesn't exist
        if not hasattr(self, 'claimed_users'):
            self.claimed_users = []

        # Loophole Fix: Check if this user already claimed from this drop
        if interaction.user.id in self.claimed_users:
            return await interaction.response.send_message("❌ You have already claimed a card from this drop!", ephemeral=True)

        if self.remaining <= 0:
            return await interaction.response.send_message("All cards claimed!", ephemeral=True)
        
        # Give card to user
        cursor.execute('''INSERT INTO inventory (user_id, card_id, quantity) VALUES (?, ?, 1) 
                          ON CONFLICT(user_id, card_id) DO UPDATE SET quantity = quantity + 1''', 
                       (str(interaction.user.id), self.card[0]))
        conn.commit()
        
        # Track that this user has now claimed
        self.claimed_users.append(interaction.user.id)
        self.remaining -= 1
        
        # Congratulations message
        congrats_embed = discord.Embed(
            description=f"Congratulations 🎉 {interaction.user.mention} won **{self.card[1]} ({self.card[2]})** from the drop!",
            color=0xFFFF00 
        )
        await interaction.channel.send(embed=congrats_embed)

        # Update the drop message
        if self.remaining <= 0:
            button.disabled, button.label = True, "Claimed Out"
            await interaction.message.edit(view=self)
        else:
            embed = interaction.message.embeds[0]
            embed.set_field_at(0, name=embed.fields[0].name, 
                               value=f"**Rarity:** {self.card[2]}\n**Value:** {self.card[3]} 🪙\n**Quantity Remaining:** {self.remaining}", 
                               inline=False)
            await interaction.message.edit(embed=embed, view=self)
        
        # Acknowledge the interaction
        if not interaction.response.is_done():
            await interaction.response.defer()
        

# NEW: SaleView for DM trading
class SaleView(ui.View):
    def __init__(self, seller, buyer, card, price, quantity):
        super().__init__(timeout=3600)
        self.seller, self.buyer, self.card, self.price, self.qty = seller, buyer, card, price, quantity

    @ui.button(label="✅ Accept", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: ui.Button):
        total = self.price * self.qty
        cursor.execute('SELECT balance FROM users WHERE id = ?', (str(self.buyer.id),))
        row = cursor.fetchone()
        if not row or row[0] < total:
            return await interaction.response.send_message(f"❌ Low balance! Need {total} 🪙", ephemeral=True)
        cursor.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (total, str(self.buyer.id)))
        cursor.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (total, str(self.seller.id)))
        cursor.execute('UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND card_id = ?', (self.qty, str(self.seller.id), self.card[0]))
        cursor.execute('INSERT INTO inventory (user_id, card_id, quantity) VALUES (?, ?, ?) ON CONFLICT(user_id, card_id) DO UPDATE SET quantity = quantity + ?', (str(self.buyer.id), self.card[0], self.qty, self.qty))
        cursor.execute('DELETE FROM inventory WHERE quantity <= 0')
        conn.commit()
        await interaction.response.send_message(f"✅ Bought {self.qty}x {self.card[1]}!")
        await self.seller.send(f"💰 {self.buyer.name} bought your cards for {total} 🪙!")
        self.stop()

    @ui.button(label="❌ Deny", style=discord.ButtonStyle.red)
    async def deny(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("Trade declined.")
        await self.seller.send(f"❌ {self.buyer.name} declined the offer.")
        self.stop()


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

class TradeView(ui.View):
    def __init__(self, sender, receiver, sender_card, receiver_card):
        super().__init__(timeout=120)
        self.sender = sender
        self.receiver = receiver
        self.sender_card = sender_card # (id, name)
        self.receiver_card = receiver_card # (id, name)
        self.accepted = False

    @ui.button(label="Accept Trade", style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.receiver.id:
            return await interaction.response.send_message("Only the trade receiver can accept this!", ephemeral=True)
        
        # Execute the trade logic
        # Remove from sender, give to receiver
        cursor.execute('UPDATE inventory SET quantity = quantity - 1 WHERE user_id = ? AND card_id = ?', (str(self.sender.id), self.sender_card[0]))
        cursor.execute('INSERT INTO inventory (user_id, card_id, quantity) VALUES (?, ?, 1) ON CONFLICT(user_id, card_id) DO UPDATE SET quantity = quantity + 1', (str(self.receiver.id), self.sender_card[0]))
        
        # Remove from receiver, give to sender
        cursor.execute('UPDATE inventory SET quantity = quantity - 1 WHERE user_id = ? AND card_id = ?', (str(self.receiver.id), self.receiver_card[0]))
        cursor.execute('INSERT INTO inventory (user_id, card_id, quantity) VALUES (?, ?, 1) ON CONFLICT(user_id, card_id) DO UPDATE SET quantity = quantity + 1', (str(self.sender.id), self.receiver_card[0]))
        # Clean up empty slots
        cursor.execute('DELETE FROM inventory WHERE quantity <= 0')
        conn.commit()

        self.accepted = True
        self.stop()
        await interaction.response.edit_message(content=f"🤝 **Trade Complete!** {self.sender.mention} and {self.receiver.mention} have swapped cards.", view=None)

    @ui.button(label="Decline", style=discord.ButtonStyle.red)
    async def decline(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id not in [self.sender.id, self.receiver.id]:
            return await interaction.response.send_message("This isn't your trade!", ephemeral=True)
        self.stop()
        await interaction.response.edit_message(content="❌ Trade cancelled.", view=None)
                         

class MarketPaginator(ui.View):
    def __init__(self, listings, client):
        super().__init__(timeout=120)
        self.listings = listings
        self.current_page = 0
        self.client = client
        
        # Hide confirm/cancel buttons initially
        self.remove_item(self.btn_confirm)
        self.remove_item(self.btn_cancel)

    async def create_embed(self):
        item = self.listings[self.current_page]
        # item: (selling_id, seller_id, price, qty, card_id, name, rarity, value, image)
        selling_id, seller_id, price, qty = item[0], item[1], item[2], item[3]
        card_id, name, rarity, value, image = item[4], item[5], item[6], item[7], item[8]
        total_amount = price * qty

        cursor.execute('SELECT color FROM rarities WHERE name = ?', (rarity,))
        res = cursor.fetchone()
        color = int(res[0].replace("#", ""), 16) if res else 0x3498db

        cursor.execute('SELECT COUNT(DISTINCT user_id) FROM inventory WHERE card_id = ?', (card_id,))
        owners = cursor.fetchone()[0]

        # FIX 3: Fetch the user directly from Discord if they aren't in the bot's temporary cache
        try:
            seller = self.client.get_user(int(seller_id)) or await self.client.fetch_user(int(seller_id))
            seller_name = seller.name
        except:
            seller_name = "Unknown User"

        embed = discord.Embed(title="🛒 Global Market", color=color)
        embed.description = f"**Page {self.current_page + 1} of {len(self.listings)}**"
        embed.add_field(name=f"**{name}**", value=(
            f"**Rarity:** {rarity}\n"
            f"**Value:** {value} 🪙\n"
            f"**Owners:** {owners} 👥\n"
            f"**Selling Amount:** {price} 🪙\n"
            f"**Quantity:** {qty}\n"
            f"**Total Amount:** {total_amount} 🪙\n"
            f"**Seller:** {seller_name}\n"
            f"**Card ID:** `{card_id}`"
        ), inline=False)
        embed.set_image(url=image)
        embed.set_footer(text=f"Selling ID: {selling_id}")
        return embed

    @ui.button(label="⬅️", style=discord.ButtonStyle.grey, custom_id="prev")
    async def btn_prev(self, interaction: discord.Interaction, button: ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            await interaction.response.edit_message(embed=await self.create_embed(), view=self)
        else: await interaction.response.defer()

    @ui.button(label="Buy", style=discord.ButtonStyle.green, custom_id="buy")
    async def btn_buy(self, interaction: discord.Interaction, button: ui.Button):
        # Swap buttons
        self.remove_item(self.btn_prev)
        self.remove_item(self.btn_buy)
        self.remove_item(self.btn_next)
        self.add_item(self.btn_confirm)
        self.add_item(self.btn_cancel)
        await interaction.response.edit_message(view=self)

    @ui.button(label="➡️", style=discord.ButtonStyle.grey, custom_id="next")
    async def btn_next(self, interaction: discord.Interaction, button: ui.Button):
        if self.current_page < len(self.listings) - 1:
            self.current_page += 1
            await interaction.response.edit_message(embed=await self.create_embed(), view=self)
        else: await interaction.response.defer()

    @ui.button(label="Confirm", style=discord.ButtonStyle.green, custom_id="confirm")
    async def btn_confirm(self, interaction: discord.Interaction, button: ui.Button):
        item = self.listings[self.current_page]
        selling_id, seller_id, price, qty = item[0], item[1], item[2], item[3]
        card_id, name, rarity, value, image = item[4], item[5], item[6], item[7], item[8]
        total_amount = price * qty

        cursor.execute('SELECT * FROM market WHERE selling_id = ?', (selling_id,))
        if not cursor.fetchone():
            await interaction.response.send_message(embed=discord.Embed(description="⚠️ This item was already sold or removed!", color=discord.Color.red()), ephemeral=True)
            try: await interaction.message.delete()
            except: pass
            return

        if str(interaction.user.id) == str(seller_id):
            return await interaction.response.send_message(embed=discord.Embed(description="⚠️ You cannot buy your own listing!", color=discord.Color.red()), ephemeral=True)

        cursor.execute('SELECT balance FROM users WHERE id = ?', (str(interaction.user.id),))
        row = cursor.fetchone()
        balance = row[0] if row else 0

        # FIX 2: Respond with the red embed FIRST, then delete the market menu
        if balance < total_amount:
            err_embed = discord.Embed(description=f"{interaction.user.mention}, you don't have enough balance to buy that item.\n**Your balance:** {balance} 🪙", color=discord.Color.red())
            await interaction.response.send_message(embed=err_embed, ephemeral=True)
            try: await interaction.message.delete()
            except: pass
            return

        # Process Transaction
        cursor.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (total_amount, str(interaction.user.id)))
        cursor.execute('INSERT INTO users (id, balance) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET balance = balance + ?', (str(seller_id), total_amount, total_amount))
        cursor.execute('DELETE FROM market WHERE selling_id = ?', (selling_id,))
        cursor.execute('INSERT INTO inventory (user_id, card_id, quantity) VALUES (?, ?, ?) ON CONFLICT(user_id, card_id) DO UPDATE SET quantity = quantity + ?', (str(interaction.user.id), card_id, qty, qty))
        conn.commit()

        # FIX 1: Send the public message to the channel, acknowledge the button, THEN delete the market menu
        pub_embed = discord.Embed(description=f"🎉 {interaction.user.mention} bought **{name} ({rarity})** from the market for **{total_amount}** 🪙.", color=discord.Color.green())
        pub_embed.add_field(name="Card Details", value=f"**Card Name:** {name}\n**Rarity:** {rarity}\n**Value:** {value}\n**Card Id:** `{card_id}`\n**Quantity:** {qty}\n**Amount:** {total_amount} 🪙", inline=False)
        pub_embed.set_image(url=image)
        
        await interaction.channel.send(embed=pub_embed)
        await interaction.response.send_message("✅ Purchase successful!", ephemeral=True)
        try: await interaction.message.delete()
        except: pass

    @ui.button(label="Cancel", style=discord.ButtonStyle.red, custom_id="cancel")
    async def btn_cancel(self, interaction: discord.Interaction, button: ui.Button):
        # Swap back to normal buttons
        self.remove_item(self.btn_confirm)
        self.remove_item(self.btn_cancel)
        self.add_item(self.btn_prev)
        self.add_item(self.btn_buy)
        self.add_item(self.btn_next)
        await interaction.response.edit_message(view=self)



class HelpPaginator(ui.View):
    def __init__(self, pages):
        super().__init__(timeout=60)
        self.pages = pages
        self.current_page = 0

    def create_embed(self):
        embed = discord.Embed(title="📜 Bot Help Menu", color=0xFFFF00)
        embed.description = f"**Page {self.current_page + 1} of {len(self.pages)}**\n\n{self.pages[self.current_page]}"
        return embed

    @ui.button(label="⬅️", style=discord.ButtonStyle.grey)
    async def prev(self, interaction: discord.Interaction, button: ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            await interaction.response.edit_message(embed=self.create_embed(), view=self)
        else: await interaction.response.defer()

    @ui.button(label="➡️", style=discord.ButtonStyle.grey)
    async def next(self, interaction: discord.Interaction, button: ui.Button):
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
            await interaction.response.edit_message(embed=self.create_embed(), view=self)
        else: await interaction.response.defer()
        

class BulkGachaView(discord.ui.View):
    def __init__(self, user, results, total_pulls):
        super().__init__(timeout=60)
        self.user = user
        self.results = results  # List of dictionaries containing card details
        self.total_pulls = total_pulls
        self.current_page = 0

    def create_embed(self):
        card = self.results[self.current_page]
        # Create embed with the rarity color
        color_hex = card['color'].replace("#", "0x")
        embed = discord.Embed(
            title="✨ GACHA PULL ✨",
            description=f"Page {self.current_page + 1}/{self.total_pulls}",
            color=discord.Color(int(color_hex, 16))
        )
        embed.add_field(name="Name", value=card['name'], inline=True)
        embed.add_field(name="Rarity", value=card['rarity'], inline=True)
        embed.add_field(name="Value", value=f"{card['value']} 🪙", inline=True)
        embed.add_field(name="Card ID", value=f"`{card['card_id']}`", inline=True)
        
        if card['image']:
            embed.set_image(url=card['image'])
        
        embed.set_footer(text=f"Collector: {self.user.name}")
        return embed

    @discord.ui.button(label="⬅️", style=discord.ButtonStyle.gray)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.user:
            await interaction.response.send_message("This gacha result isn't for you!", ephemeral=True)
            return
        
        self.current_page = (self.current_page - 1) % self.total_pulls
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    @discord.ui.button(label="➡️", style=discord.ButtonStyle.gray)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.user:
            await interaction.response.send_message("This gacha result isn't for you!", ephemeral=True)
            return
        
        self.current_page = (self.current_page + 1) % self.total_pulls
        await interaction.response.edit_message(embed=self.create_embed(), view=self)
        

# --- 5. BOT SETUP ---
class GachaBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # This syncs commands when the bot starts up
        await self.tree.sync()
        print("Slash commands synced via setup_hook")

client = GachaBot()

@client.event
async def on_ready():
    print(f'Logged in as {client.user} (ID: {client.user.id})')
    print('------')
    # This is the "Force Sync" that fixes the "Not Responding" error
    try:
        synced = await client.tree.sync()
        print(f"Synced {len(synced)} command(s) successfully!")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
        

@client.event
async def on_message(message):
    if message.author.bot: return
    c = random.randint(10, 50)
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

@client.tree.command(name="inventory", description="View your collection")
async def inventory(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    cursor.execute('SELECT c.*, i.quantity FROM inventory i JOIN cards c ON i.card_id = c.card_id WHERE i.user_id = ?', (str(interaction.user.id),))
    items = cursor.fetchall()
    if not items: return await interaction.followup.send("Inventory empty.")
    view = CardPaginator(items, 0, "Your Collection")
    await interaction.followup.send(embed=view.create_embed(), view=view)

# --- 1. /addcoin (Admin) ---

# --- 2. /view_card (Member) ---
@client.tree.command(name="view_card", description="View details of a specific card")
async def view_card(interaction: discord.Interaction, query: str):
    await interaction.response.defer(ephemeral=True)
    
    # Search by Name or ID
    cursor.execute('SELECT * FROM cards WHERE name = ? OR card_id = ?', (query, query))
    card = cursor.fetchone()
    if not card: 
        return await interaction.followup.send("❌ Card not found.")
    
    # Uses your original Paginator logic to create the embed
    view = CardPaginator([card], 0, "Card Details")
    await interaction.followup.send(embed=view.create_embed())
    

# --- 4. /inspect_inventory (Admin) ---

# --- 5. /rarity_list (Member) ---

@client.tree.command(name="rarity_list", description="View rarity drop chances (Public)")
async def rarity_list(interaction: discord.Interaction):
    # Removed ephemeral=True so it is visible to all
    cursor.execute('SELECT name, chance FROM rarities ORDER BY chance DESC')
    rows = cursor.fetchall()
    desc = "\n".join([f"✨ **{r[0]}**: {r[1]}%" for r in rows])
    
    embed = discord.Embed(title="Rarity Tiers & Drop Chances", description=desc, color=0xFFD700)
    await interaction.response.send_message(embed=embed)
#--- 1. /gacha (Member) ---

@client.tree.command(name="gacha", description="Spend coins to pull a random card")
async def gacha(interaction: discord.Interaction):
    await interaction.response.defer()
    
    # Fetch dynamic cost from config
    cursor.execute("SELECT value FROM config WHERE key = 'gacha_cost'")
    res = cursor.fetchone()
    cost = int(res[0]) if res else 1000 # Default to 1000 if not set

    # Check user balance
    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(interaction.user.id),))
    row = cursor.fetchone()
    balance = row[0] if row else 0

    if balance < cost:
        embed = discord.Embed(description=f"❌ You need **{cost}** 🪙 to pull!\n**Your balance:** {balance} 🪙", color=discord.Color.red())
        return await interaction.followup.send(embed=embed)

    # Weighted Random Rarity Logic
    cursor.execute('SELECT name, chance FROM rarities')
    rarity_data = cursor.fetchall()
    if not rarity_data:
        return await interaction.followup.send("⚠️ No rarities have been set up yet! Ask an admin to use `/add_rarity`.")

    rarities = [r[0] for r in rarity_data]
    weights = [r[1] for r in rarity_data]
    chosen_rarity = random.choices(rarities, weights=weights, k=1)[0]
    
    # Pick a random card from that rarity
    cursor.execute('SELECT * FROM cards WHERE rarity = ? ORDER BY RANDOM() LIMIT 1', (chosen_rarity,))
    card = cursor.fetchone()

    if not card:
        return await interaction.followup.send(f"⚠️ The gacha machine jammed! No cards found for rarity: **{chosen_rarity}**.")

    # Deduct coins and Give card
    cursor.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (cost, str(interaction.user.id)))
    cursor.execute('''INSERT INTO inventory (user_id, card_id, quantity) VALUES (?, ?, 1) 
                      ON CONFLICT(user_id, card_id) DO UPDATE SET quantity = quantity + 1''', (str(interaction.user.id), card[0]))
    conn.commit()

    # Get rarity color for embed
    cursor.execute('SELECT color FROM rarities WHERE name = ?', (card[2],))
    color_res = cursor.fetchone()
    embed_color = int(color_res[0].replace("#", ""), 16) if color_res else 0xFFFF00

    embed = discord.Embed(title="✨ GACHA PULL ✨", color=embed_color)
    embed.add_field(name=f"**{card[1]}**", value=f"**Rarity:** {card[2]}\n**Value:** {card[3]} 🪙\n**Card ID:** `{card[0]}`", inline=False)
    embed.set_image(url=card[4])
    embed.set_footer(text=f"Remaining Balance: {balance - cost} 🪙")
    
    await interaction.followup.send(content=f"🎉 {interaction.user.mention} pulled a card!", embed=embed)
    

# --- 6. COMMANDS (REPLACEMENTS) ---


@client.tree.command(name="trade", description="Sell cards for coins via DM")
async def trade(interaction: discord.Interaction, user: discord.Member, card_name_or_id: str, trade_amount: int, quantity: int):
    await interaction.response.defer(ephemeral=True)
    cursor.execute('SELECT c.*, i.quantity FROM inventory i JOIN cards c ON i.card_id = c.card_id WHERE i.user_id = ? AND (c.name = ? OR c.card_id = ?)', (str(interaction.user.id), card_name_or_id, card_name_or_id))
    card = cursor.fetchone()
    if not card or card[5] < quantity: return await interaction.followup.send("❌ You don't have enough copies!")
    embed = discord.Embed(title="🤝 Trade Offer", color=discord.Color.blue())
    embed.add_field(name="Details", value=f"**Seller:** {interaction.user.name}\n**Card:** {card[1]}\n**Qty:** {quantity}\n**Total:** {trade_amount * quantity} 🪙")
    embed.set_image(url=card[4])
    try:
        await user.send(embed=embed, view=SaleView(interaction.user, user, card, trade_amount, quantity))
        await interaction.followup.send(f"✅ Offer sent to {user.name}!")
    except: await interaction.followup.send("❌ User has DMs closed!")

@client.tree.command(name="card_list", description="Admin: Sorted card list")
async def card_list(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild: 
        return await interaction.followup.send("❌ Admin!")
    
    cursor.execute('SELECT * FROM cards')
    cards = cursor.fetchall()
    
    # Restores your original rarity sorting
    sorted_cards = sorted(cards, key=lambda x: RARITY_ORDER.get(x[2], 99))
    
    view = CardPaginator(sorted_cards, 0, "Global List")
    await interaction.followup.send(embed=view.create_embed(), view=view)


@client.tree.command(name="burn", description="Burn your cards to receive 50% of their value in coins")
@app_commands.describe(card_name="Name or ID of the card to burn", quantity="How many to burn")
async def burn(interaction: discord.Interaction, card_name: str, quantity: int = 1):
    if quantity <= 0:
        await interaction.response.send_message("Quantity must be at least 1.", ephemeral=True)
        return

    user_id = str(interaction.user.id)
    
    # 1. Fetch card details from the database
    cursor.execute("SELECT card_id, name, rarity, value, image FROM cards WHERE name = ? OR card_id = ?", (card_name, card_name))
    card = cursor.fetchone()
    
    if not card:
        embed = discord.Embed(description="❌ You don't have that card", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    c_id, name, rarity, value, image = card
    burn_value_per_card = int(value * 0.5)
    total_received = burn_value_per_card * quantity

    # 2. Check user's inventory to ensure they have the card and the quantity
    cursor.execute("SELECT quantity FROM inventory WHERE user_id = ? AND card_id = ?", (user_id, c_id))
    inv_data = cursor.fetchone()

    if not inv_data or inv_data[0] < quantity:
        embed = discord.Embed(description="❌ You don't have enough cards", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    try:
        # 3. Update Inventory: Delete if burning all copies, otherwise reduce quantity
        if inv_data[0] == quantity:
            cursor.execute("DELETE FROM inventory WHERE user_id = ? AND card_id = ?", (user_id, c_id))
        else:
            cursor.execute("UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND card_id = ?", (quantity, user_id, c_id))

        # 4. Add the calculated coins to the user's balance
        cursor.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (total_received, user_id))
        conn.commit()

        # 5. Success Embed (Visible to everyone in the channel)
        embed = discord.Embed(description=f"**{interaction.user.name}** successfully burned cards", color=discord.Color.green())
        embed.add_field(name="Name", value=name, inline=True)
        embed.add_field(name="Rarity", value=rarity, inline=True)
        embed.add_field(name="Id", value=f"`{c_id}`", inline=True)
        embed.add_field(name="Value", value=f"{value} 🪙", inline=True)
        embed.add_field(name="Quantity", value=str(quantity), inline=True)
        embed.add_field(name="Amount received", value=f"{total_received} 🪙", inline=True)
        if image:
            embed.set_image(url=image)

        await interaction.response.send_message(embed=embed)

    except Exception as e:
        await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)
    

# --- MARKET SYSTEM COMMANDS ---
@client.tree.command(name="market_sell", description="List a card for sale on the market")
async def market_sell(interaction: discord.Interaction, card_name: str, price: int, quantity: int = 1):
    if price < 0 or quantity <= 0:
        return await interaction.response.send_message("❌ Invalid price or quantity.", ephemeral=True)

    # Check if user actually has the card and enough quantity
    cursor.execute('''SELECT i.quantity, c.card_id, c.name FROM inventory i 
                      JOIN cards c ON i.card_id = c.card_id 
                      WHERE i.user_id = ? AND (c.name = ? OR c.card_id = ?)''', 
                   (str(interaction.user.id), card_name, card_name))
    row = cursor.fetchone()

    if not row or row[0] < quantity:
        return await interaction.response.send_message("❌ You don't have enough of that card to sell!", ephemeral=True)

    card_id, real_name = row[1], row[2]

    # 1. Remove from inventory FIRST (Prevents the loophole)
    cursor.execute('UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND card_id = ?', 
                   (quantity, str(interaction.user.id), card_id))
    cursor.execute('DELETE FROM inventory WHERE quantity <= 0') # Clean up empty slots

    # 2. Add to market
    cursor.execute('INSERT INTO market (seller_id, card_id, price, quantity) VALUES (?, ?, ?, ?)', 
                   (str(interaction.user.id), card_id, price, quantity))
    conn.commit()

    await interaction.response.send_message(f"✅ Listed {quantity}x **{real_name}** for {price} 🪙 each.")
    

@client.tree.command(name="market", description="Browse the global card market")
async def market(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True) # Only the user can see it!
    
    cursor.execute('''SELECT m.selling_id, m.seller_id, m.price, m.quantity, 
                             c.card_id, c.name, c.rarity, c.value, c.image 
                      FROM market m JOIN cards c ON m.card_id = c.card_id''')
    listings = cursor.fetchall()

    if not listings:
        return await interaction.followup.send(embed=discord.Embed(description="🛒 The market is currently empty!", color=discord.Color.orange()))

    view = MarketPaginator(listings, client)
    await interaction.followup.send(embed=await view.create_embed(), view=view)

@client.tree.command(name="remove_market", description="Remove your card from the market")
async def remove_market(interaction: discord.Interaction, id: int):
    await interaction.response.defer(ephemeral=True)

    cursor.execute('''SELECT m.seller_id, m.card_id, m.quantity, c.name FROM market m 
                      JOIN cards c ON m.card_id = c.card_id WHERE m.selling_id = ?''', (id,))
    listing = cursor.fetchone()

    if not listing:
        return await interaction.followup.send(embed=discord.Embed(description="⚠️ Market listing not found. Double-check the Selling ID.", color=discord.Color.red()))

    seller_id, card_id, qty, card_name = listing[0], listing[1], listing[2], listing[3]

    if str(interaction.user.id) != str(seller_id):
        err_embed = discord.Embed(description=f"{interaction.user.mention}, You can't remove someone else's card.", color=discord.Color.red())
        return await interaction.followup.send(embed=err_embed)

    # Return cards to inventory and remove from market
    cursor.execute('INSERT INTO inventory (user_id, card_id, quantity) VALUES (?, ?, ?) ON CONFLICT(user_id, card_id) DO UPDATE SET quantity = quantity + ?', (str(interaction.user.id), card_id, qty, qty))
    cursor.execute('DELETE FROM market WHERE selling_id = ?', (id,))
    conn.commit()

    success_embed = discord.Embed(description=f"{interaction.user.mention}, Successfully removed **{card_name}** from the market. The cards have been returned to your inventory.", color=discord.Color.green())
    await interaction.followup.send(embed=success_embed)
    
# --- PART 6: GIFTING & LEADERBOARDS ---

@client.tree.command(name="gift_card", description="Gift a card to a user for free")
async def gift_card(interaction: discord.Interaction, user: discord.Member, card_name: str, quantity: int):
    await interaction.response.defer(ephemeral=True)
    
    if user.id == interaction.user.id:
        return await interaction.followup.send("❌ You can't gift cards to yourself!")
    if quantity <= 0:
        return await interaction.followup.send("❌ Quantity must be at least 1!")

    # Check if sender has the card and enough quantity
    cursor.execute('''SELECT c.card_id, i.quantity, c.name, c.rarity, c.value, c.image 
                      FROM inventory i JOIN cards c ON i.card_id = c.card_id 
                      WHERE i.user_id = ? AND (c.name = ? OR c.card_id = ?)''', 
                   (str(interaction.user.id), card_name, card_name))
    card = cursor.fetchone()

    if not card:
        err_embed = discord.Embed(description=f"{interaction.user.mention} ⚠️ You don't have that card in inventory", color=discord.Color.red())
        return await interaction.followup.send(embed=err_embed)
    
    if card[1] < quantity:
        err_embed = discord.Embed(description=f"{interaction.user.mention} ⚠️ You don't have that much card in inventory", color=discord.Color.red())
        return await interaction.followup.send(embed=err_embed)

    card_id, _, name, rarity, value, image = card

    # Transfer logic
    cursor.execute('UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND card_id = ?', (quantity, str(interaction.user.id), card_id))
    cursor.execute('INSERT INTO inventory (user_id, card_id, quantity) VALUES (?, ?, ?) ON CONFLICT(user_id, card_id) DO UPDATE SET quantity = quantity + ?', (str(user.id), card_id, quantity, quantity))
    cursor.execute('DELETE FROM inventory WHERE quantity <= 0')
    conn.commit()

    # Get total owners for the embed
    cursor.execute('SELECT COUNT(DISTINCT user_id) FROM inventory WHERE card_id = ?', (card_id,))
    owners = cursor.fetchone()[0]

    # DM to receiver
    dm_embed = discord.Embed(description=f"{interaction.user.mention} has gifted you **{name}** 🎁", color=discord.Color.green())
    dm_embed.add_field(name="Details", value=(
        f"**Name of card:** {name}\n"
        f"**Rarity:** {rarity}\n"
        f"**Value:** {value} 🪙\n"
        f"**Card id:** `{card_id}`\n"
        f"**Quantity:** {quantity}\n"
        f"**Owners:** {owners} 👥"
    ), inline=False)
    dm_embed.set_image(url=image)

    try:
        await user.send(embed=dm_embed)
        await interaction.followup.send(f"✅ Successfully gifted {quantity}x {name} to {user.name}!")
    except discord.Forbidden:
        await interaction.followup.send(f"✅ Successfully gifted to {user.name}, but their DMs are closed so I couldn't notify them.")

@client.tree.command(name="gift_coin", description="Gift coins to a user for free")
async def gift_coin(interaction: discord.Interaction, user: discord.Member, amount: int):
    await interaction.response.defer(ephemeral=True)
    
    if user.id == interaction.user.id:
        return await interaction.followup.send("❌ You can't gift coins to yourself!")
    if amount <= 0:
        return await interaction.followup.send("❌ You must gift at least 1 coin!")

    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(interaction.user.id),))
    row = cursor.fetchone()
    balance = row[0] if row else 0

    if balance < amount:
        err_embed = discord.Embed(description=f"{interaction.user.mention} ⚠️ You don't have enough balance\n**Balance:** {balance} 🪙", color=discord.Color.red())
        return await interaction.followup.send(embed=err_embed)

    # Transfer logic
    cursor.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (amount, str(interaction.user.id)))
    cursor.execute('INSERT INTO users (id, balance) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET balance = balance + ?', (str(user.id), amount, amount))
    conn.commit()

    # Get receiver's new balance
    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(user.id),))
    receiver_balance = cursor.fetchone()[0]

    # DM to receiver
    dm_embed = discord.Embed(description=f"{interaction.user.mention} has gifted you **{amount}** 🪙 coins 🎁\n**Balance:** {receiver_balance} 🪙", color=discord.Color.green())

    try:
        await user.send(embed=dm_embed)
        await interaction.followup.send(f"✅ Successfully gifted {amount} coins to {user.name}!")
    except discord.Forbidden:
        await interaction.followup.send(f"✅ Successfully gifted to {user.name}, but their DMs are closed so I couldn't notify them.")

@client.tree.command(name="balance_rank", description="View the top 10 users with the highest balance")
async def balance_rank(interaction: discord.Interaction):
    await interaction.response.defer() # No ephemeral=True here, so everyone can see it!

    cursor.execute('SELECT id, balance FROM users ORDER BY balance DESC LIMIT 10')
    top_users = cursor.fetchall()

    if not top_users:
        return await interaction.followup.send(embed=discord.Embed(description="No users have coins yet!", color=0xFFFF00))

    desc = ""
    for i, (user_id, balance) in enumerate(top_users, 1):
        try:
            # Force discord to fetch the username even if they are offline
            user_obj = interaction.client.get_user(int(user_id)) or await interaction.client.fetch_user(int(user_id))
            name = user_obj.name
        except:
            name = "Unknown User"
        
        desc += f"**{i})** {name} - {balance} 🪙\n"

    embed = discord.Embed(title="🏆 Wealth Leaderboard", description=desc, color=0xFFFF00) # Yellow color
    await interaction.followup.send(embed=embed)
        

# --- PART 7: ADMIN UTILITIES ---


# --- PART 8: ADMIN MANAGEMENT COMMANDS ---

# --- PART 9: FINAL FEATURES ---



@client.tree.command(name="account", description="Set your account privacy")
@app_commands.choices(status=[
    app_commands.Choice(name="Public", value="public"),
    app_commands.Choice(name="Private", value="private")
])
async def account(interaction: discord.Interaction, status: app_commands.Choice[str]):
    cursor.execute('INSERT INTO users (id, account_status) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET account_status = ?', (str(interaction.user.id), status.value, status.value))
    conn.commit()
    await interaction.response.send_message(f"✅ Your account is now **{status.name}**.", ephemeral=True)

@client.tree.command(name="balance", description="Check your coin balance")
async def balance(interaction: discord.Interaction):
    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(interaction.user.id),))
    row = cursor.fetchone()
    bal = row[0] if row else 0
    embed = discord.Embed(title=f"{interaction.user.name}'s balance", description=f"**Balance:** {bal} 🪙", color=0xFFFF00)
    await interaction.response.send_message(embed=embed)

@client.tree.command(name="user_balance", description="Check another member's balance")
async def user_balance(interaction: discord.Interaction, user: discord.Member):
    cursor.execute('SELECT balance, account_status FROM users WHERE id = ?', (str(user.id),))
    row = cursor.fetchone()
    if row and row[1] == 'private' and interaction.user.id != user.id:
        embed = discord.Embed(description=f"❌ {user.mention}'s account is private.\nYou can't get details of that account.", color=discord.Color.red())
        return await interaction.response.send_message(embed=embed, ephemeral=True)
    
    bal = row[0] if row else 0
    embed = discord.Embed(title=f"{user.name}'s balance", description=f"**Balance:** {bal} 🪙", color=0xFFFF00)
    await interaction.response.send_message(embed=embed)

@client.tree.command(name="user_inventory", description="Check another member's inventory")
async def user_inventory(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer() # <--- Added this to fix the timeout
    
    cursor.execute('SELECT account_status FROM users WHERE id = ?', (str(user.id),))
    row = cursor.fetchone()
    
    # Check privacy
    if row and row[0] == 'private' and interaction.user.id != user.id:
        embed = discord.Embed(description=f"❌ {user.mention}'s account is private.\nYou can't get details of that account.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed)
    
    cursor.execute('''SELECT c.card_id, c.name, c.rarity, c.value, c.image, i.quantity 
                      FROM inventory i 
                      JOIN cards c ON i.card_id = c.card_id 
                      WHERE i.user_id = ?''', (str(user.id),))
    cards = cursor.fetchall()
    
    if not cards:
        return await interaction.followup.send(f"{user.name} does not have any cards yet!")
    
    view = CardPaginator(cards, 0, f"{user.name}'s Inventory")
    await interaction.followup.send(embed=view.create_embed(), view=view)
    

@client.tree.command(name="beg", description="Ask for some spare coins")
async def beg(interaction: discord.Interaction):
    await interaction.response.defer() # Gives the bot 15 minutes to respond instead of 3 seconds
    
    now = datetime.datetime.now()
    cursor.execute('SELECT last_beg, balance FROM users WHERE id = ?', (str(interaction.user.id),))
    row = cursor.fetchone()
    
    if row and row[0]:
        last_time = datetime.datetime.fromisoformat(row[0])
        if now < last_time + datetime.timedelta(minutes=30):
            diff = (last_time + datetime.timedelta(minutes=30)) - now
            minutes = int(diff.total_seconds() // 60)
            embed = discord.Embed(description=f"{interaction.user.mention}\nYou can't beg now. God is busy fulfilling the wishes of others. Please wait **{minutes}** more minutes.", color=discord.Color.red())
            return await interaction.followup.send(embed=embed) # Use followup after defer

    amount = random.randint(1, 250)
    cursor.execute('INSERT INTO users (id, balance, last_beg) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET balance = balance + ?, last_beg = ?', (str(interaction.user.id), amount, now.isoformat(), amount, now.isoformat()))
    conn.commit()
    
    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(interaction.user.id),))
    new_bal = cursor.fetchone()[0]
    
    embed = discord.Embed(title=f"{interaction.user.name}", description=f"God showed mercy on you. You received **{amount}** 🪙 coins!\n**Balance:** {new_bal} 🪙", color=0xFFFF00)
    await interaction.followup.send(embed=embed)

@client.tree.command(name="daily", description="Claim your daily reward")
async def daily(interaction: discord.Interaction):
    await interaction.response.defer() # Added defer here too
    
    now = datetime.datetime.now()
    cursor.execute('SELECT last_daily, balance FROM users WHERE id = ?', (str(interaction.user.id),))
    row = cursor.fetchone()
    
    if row and row[0]:
        last_time = datetime.datetime.fromisoformat(row[0])
        if now.date() == last_time.date():
            tomorrow = datetime.datetime.combine(now.date() + datetime.timedelta(days=1), datetime.time.min)
            diff = tomorrow - now
            hours, remainder = divmod(int(diff.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            embed = discord.Embed(description=f"{interaction.user.mention}\nYou've already claimed your daily reward. Please wait **{hours}h {minutes}m** to claim again.", color=discord.Color.red())
            return await interaction.followup.send(embed=embed)

    amount = random.randint(500, 1000)
    cursor.execute('INSERT INTO users (id, balance, last_daily) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET balance = balance + ?, last_daily = ?', (str(interaction.user.id), amount, now.isoformat(), amount, now.isoformat()))
    conn.commit()
    
    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(interaction.user.id),))
    new_bal = cursor.fetchone()[0]
    
    embed = discord.Embed(description=f"{interaction.user.mention} claimed their daily reward!\n**Amount:** {amount} 🪙\n**Balance:** {new_bal} 🪙", color=0xFFFF00)
    await interaction.followup.send(embed=embed)

@client.tree.command(name="help", description="List all available commands and how to play")
async def help(interaction: discord.Interaction):
    pages = [
        # Page 1: Welcome Page
        "# **Welcome to Anime TCG**\n\nYou can collect your Anime TCG in the #**Anime TCG** channel. You can earn coins by chatting with others and by using member commands. If you find any problem or bug in the Anime TCG you can report it to the owner. Play responsibly and start collecting.",
        
        # Page 2: Economy & Basics
        "**💰 Economy & Basics**\n\n**1. `/balance`**\nCheck your coin balance.\n\n**2. `/beg`**\nAsk for coins (30m cooldown).\n\n**3. `/daily`**\nClaim daily coins (resets at midnight).\n\n**4. `/account`**\nSet your profile to Public or Private.\n\n**5. `/rank`**\nCheck your current level.\n\n**6. `/burn`**\nGet 50% of card value by burning card.",
        
        # Page 3: Gacha & Collecting
        "**🎴 Gacha & Collecting**\n\n**7. `/gacha`**\nSpend coins to pull a random card.\n\n**8. `/inventory`**\nView your card collection.\n\n**9. `/card_list`**\nView all available cards in the bot.\n\n**10. `/view_card`**\nInspect a specific card's details and image.\n\n**11. `/rarity_list`**\nView all card rarities and drop chances.",
        
        # Page 4: Social & Trading
        "**🤝 Social & Trading**\n\n**12. `/user_balance <user>`**\nCheck another member's balance.\n\n**13. `/user_inventory <user>`**\nView another member's collection.\n\n**14. `/gift_card`**\nGive a card to another player.\n\n**15. `/gift_coin`**\nGive coins to another player.\n\n**16. `/trade`**\nTrade cards with another player.",
        
        # Page 5: Market & Leaderboards
        "**⚖️ Market & Leaderboards**\n\n**17. `/market`**\nBrowse cards for sale.\n\n**18. `/market_sell`**\nPut a card up for sale.\n\n**19. `/remove_market`**\nCancel your market listing.\n\n**20. `/card_leaderboard`**\nSee who has the most/best cards.\n\n**21. `/user_leaderboard`**\nSee the top users overall.\n\n**22. `/balance_rank`**\nSee the richest users."
    ]
    
    view = HelpPaginator(pages)
    # ephemeral=True ensures only the sender can see this yellow embed
    await interaction.response.send_message(embed=view.create_embed(), view=view, ephemeral=True)
            

@client.tree.command(name="bulk_gacha", description="Pull multiple cards at once")
@app_commands.describe(no_of_pulls="Number of cards to pull (1-20)")
async def bulk_gacha(interaction: discord.Interaction, no_of_pulls: int):
    # 1. Validation for the Limit (20 cards)
    if no_of_pulls > 20:
        embed = discord.Embed(description="❌ You can't pull more than 20 cards at once.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if no_of_pulls <= 0:
        await interaction.response.send_message("Please enter a number greater than 0.", ephemeral=True)
        return

    user_id = str(interaction.user.id)
    gacha_cost = 1000 * no_of_pulls  # Assuming 1000 is the standard cost

    # 2. Check Balance
    cursor.execute("SELECT balance FROM users WHERE id = ?", (user_id,))
    user_data = cursor.fetchone()
    balance = user_data[0] if user_data else 0

    if balance < gacha_cost:
        embed = discord.Embed(title="Insufficient Balance", color=discord.Color.red())
        embed.description = f"Your balance is not enough.\n**Balance:** {balance} 🪙\n**Required:** {gacha_cost} 🪙"
        await interaction.response.send_message(embed=embed)
        return

    # 3. Pulling Logic
    # Fetch rarities for weighted random
    cursor.execute('SELECT name, chance, color FROM rarities')
    rarity_data = cursor.fetchall()
    rarity_names = [r[0] for r in rarity_data]
    rarity_chances = [r[1] for r in rarity_data]
    rarity_colors = {r[0]: r[2] for r in rarity_data}

    pull_results = []

    try:
        for _ in range(no_of_pulls):
            # Select Rarity
            rarity = random.choices(rarity_names, weights=rarity_chances, k=1)[0]
            
            # Select Random Card of that Rarity
            cursor.execute('SELECT card_id, name, value, image FROM cards WHERE rarity = ?', (rarity,))
            cards_of_rarity = cursor.fetchall()
            
            if not cards_of_rarity:
                continue # Skip if no cards exist for this rarity
                
            card = random.choice(cards_of_rarity)
            c_id, c_name, c_value, c_image = card

            # 4. Inventory & Owners Update
            cursor.execute('SELECT quantity FROM inventory WHERE user_id = ? AND card_id = ?', (user_id, c_id))
            inv_item = cursor.fetchone()

            if inv_item:
                cursor.execute('UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND card_id = ?', (user_id, c_id))
            else:
                cursor.execute('INSERT INTO inventory (user_id, card_id, quantity) VALUES (?, ?, 1)', (user_id, c_id))
                
            # Store result for the View
            pull_results.append({
                'card_id': c_id,
                'name': c_name,
                'rarity': rarity,
                'value': c_value,
                'image': c_image,
                'color': rarity_colors[rarity]
            })

        # 5. Deduct Balance and Commit
        cursor.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (gacha_cost, user_id))
        conn.commit()

        # 6. Send Response
        view = BulkGachaView(interaction.user, pull_results, len(pull_results))
        await interaction.response.send_message(f"🎉 {interaction.user.mention} pulled cards!", embed=view.create_embed(), view=view)

    except Exception as e:
        conn.rollback()
        await interaction.response.send_message(f"An error occurred during bulk gacha: {e}", ephemeral=True)
        

if __name__ == '__main__':
    Thread(target=run_flask).start()
    client.run(os.environ.get('DISCORD_TOKEN'))


    
