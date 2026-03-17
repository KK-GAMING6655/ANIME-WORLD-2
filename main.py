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
    
    # FIX IS HERE: Added UNIQUE(user_id, card_id) at the end
    cursor.execute('''CREATE TABLE IF NOT EXISTS inventory (
                        user_id TEXT, 
                        card_id INTEGER, 
                        quantity INTEGER DEFAULT 1, 
                        UNIQUE(user_id, card_id))''')
    
    cursor.execute('CREATE TABLE IF NOT EXISTS rarities (name TEXT PRIMARY KEY, color TEXT, chance REAL)')
    
    default_rarities = [
        ("Common", "808080", 50.0), ("Uncommon", "008000", 20.0),
        ("Rare", "0000FF", 10.0), ("Epic", "EE82EE", 5.0),
        ("Legendary", "FFFF00", 2.0), ("Super Legendary", "FF0000", 1.0)
    ]
    for name, color, chance in default_rarities:
        cursor.execute('INSERT OR IGNORE INTO rarities (name, color, chance) VALUES (?, ?, ?)', (name, color, chance))
    conn.commit()

    # Add this line right below your inventory table creation
    cursor.execute('''CREATE TABLE IF NOT EXISTS market (
                        selling_id INTEGER PRIMARY KEY, 
                        seller_id TEXT, 
                        card_id INTEGER, 
                        price INTEGER, 
                        quantity INTEGER)''')


    # Add this line to store bot settings like the default channel
    cursor.execute('CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)')
    

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

# --- 4. UI CLASSES ---


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
        color = int(res[0], 16) if res else 0x3498db

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
        if self.remaining <= 0:
            return await interaction.response.send_message("All cards claimed!", ephemeral=True)
        
        cursor.execute('INSERT INTO inventory (user_id, card_id, quantity) VALUES (?, ?, 1) ON CONFLICT(user_id, card_id) DO UPDATE SET quantity = quantity + 1', (str(interaction.user.id), self.card[0]))
        conn.commit()
        
        self.remaining -= 1
        
        # ADDED: Congratulations message in the channel
        congrats_embed = discord.Embed(
            description=f"Congratulations 🎉 {interaction.user.mention} won **{self.card[1]} ({self.card[2]})** from the drop!",
            color=0xFFFF00 # Yellow
        )
        await interaction.channel.send(embed=congrats_embed)

        if self.remaining <= 0:
            button.disabled, button.label = True, "Claimed Out"
            await interaction.message.edit(view=self)
        else:
            embed = interaction.message.embeds[0]
            embed.set_field_at(0, name=embed.fields[0].name, value=f"**Rarity:** {self.card[2]}\n**Value:** {self.card[3]} 🪙\n**Quantity Remaining:** {self.remaining}", inline=False)
            await interaction.message.edit(embed=embed, view=self)
        
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
        color = int(res[0], 16) if res else 0x3498db

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

# --- 1. /addcoin (Admin) ---
@client.tree.command(name="addcoin", description="Admin: Give coins to a user")
async def addcoin(interaction: discord.Interaction, user: discord.Member, amount: int):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild: 
        return await interaction.followup.send("❌ Admin only!")
    
    cursor.execute('INSERT INTO users (id, balance) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET balance = balance + ?', 
                   (str(user.id), amount, amount))
    conn.commit()
    await interaction.followup.send(f"✅ Added **{amount}** coins to {user.mention}!")

# --- 2. /view_card (Member) ---
@client.tree.command(name="view_card", description="View details of a specific card")
async def view_card(interaction: discord.Interaction, query: str):
    await interaction.response.defer(ephemeral=True)
    
    # Search by Name or ID
    cursor.execute('SELECT * FROM cards WHERE name = ? OR card_id = ?', (query, query))
    card = cursor.fetchone()
    if not card: 
        return await interaction.followup.send("❌ Card not found.")
    
    # Use the Paginator logic to create a single premium embed without buttons
    view = CardPaginator([card], 0, "Card Details")
    await interaction.followup.send(embed=view.create_embed())



# --- 4. /inspect_inventory (Admin) ---
@client.tree.command(name="inspect_inventory", description="Admin: View another user's collection")
async def inspect_inventory(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    if not interaction.user.guild_permissions.manage_guild: 
        return await interaction.followup.send("❌ Admin only!")
    
    cursor.execute('SELECT c.*, i.quantity FROM inventory i JOIN cards c ON i.card_id = c.card_id WHERE i.user_id = ?', (str(user.id),))
    items = cursor.fetchall()
    if not items: 
        return await interaction.followup.send(f"❌ {user.name}'s inventory is empty.")
    
    view = CardPaginator(items, 0, f"{user.name}'s Collection")
    await interaction.followup.send(embed=view.create_embed(), view=view)

# --- 5. /rarity_list (Member) ---

@client.tree.command(name="rarity_list", description="View rarity drop chances (Public)")
async def rarity_list(interaction: discord.Interaction):
    # Removed ephemeral=True so it is visible to all
    cursor.execute('SELECT name, chance FROM rarities ORDER BY chance DESC')
    rows = cursor.fetchall()
    desc = "\n".join([f"✨ **{r[0]}**: {r[1]}%" for r in rows])
    
    embed = discord.Embed(title="Rarity Tiers & Drop Chances", description=desc, color=0xFFD700)
    await interaction.response.send_message(embed=embed) 
    
        
# --- 1. /gacha (Member) ---
@client.tree.command(name="gacha", description="Spend 50 coins to pull a random card")
async def gacha(interaction: discord.Interaction):
    await interaction.response.defer()
    cost = 50
    
    # Check balance
    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(interaction.user.id),))
    row = cursor.fetchone()
    if not row or row[0] < cost:
        return await interaction.followup.send(f"❌ You need **{cost}** coins to pull! Chat more to earn coins.")

    # Deduct coins
    cursor.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (cost, str(interaction.user.id)))
    
    # Weighted Random Rarity
    cursor.execute('SELECT name, chance FROM rarities')
    rarity_data = cursor.fetchall() # List of ('Common', 50.0), etc.
    rarities = [r[0] for r in rarity_data]
    weights = [r[1] for r in rarity_data]
    
    chosen_rarity = random.choices(rarities, weights=weights, k=1)[0]
    
    # Pick a random card from that rarity
    cursor.execute('SELECT * FROM cards WHERE rarity = ? ORDER BY RANDOM() LIMIT 1', (chosen_rarity,))
    card = cursor.fetchone()

    if not card:
        # Fallback if an admin created a rarity but added no cards to it
        return await interaction.followup.send("⚠️ The gacha machine malfunctioned! (No cards found for this rarity). Your coins were refunded.")

    # Give card to user
    cursor.execute('''INSERT INTO inventory (user_id, card_id, quantity) VALUES (?, ?, 1) 
                      ON CONFLICT(user_id, card_id) DO UPDATE SET quantity = quantity + 1''', (str(interaction.user.id), card[0]))
    conn.commit()

    # Premium Reveal Embed
    # Use the logic from your CardPaginator to match style
    cursor.execute('SELECT color FROM rarities WHERE name = ?', (card[2],))
    color_res = cursor.fetchone()
    embed_color = int(color_res[0], 16) if color_res else 0x3498db

    embed = discord.Embed(title="✨ GACHA PULL ✨", color=embed_color)
    embed.add_field(name=f"**{card[1]}**", value=f"**Rarity:** {card[2]}\n**Value:** {card[3]} 🪙\n**Card ID:** `{card[0]}`", inline=False)
    embed.set_image(url=card[4])
    embed.set_footer(text=f"Remaining Balance: {row[0] - cost} 🪙")
    
    await interaction.followup.send(content=f"{interaction.user.mention} pulled a card!", embed=embed)

# --- 6. COMMANDS (REPLACEMENTS) ---

@client.tree.command(name="drop", description="Admin: Public card drop")
async def drop(interaction: discord.Interaction, name: str, quantity: int):
    if not interaction.user.guild_permissions.manage_guild: return await interaction.response.send_message("❌ Admin only!")
    cursor.execute('SELECT * FROM cards WHERE name = ? OR card_id = ?', (name, name))
    card = cursor.fetchone()
    if not card: return await interaction.response.send_message("Card not found!")
    embed = discord.Embed(title="🎁 PUBLIC DROP!", color=discord.Color.gold())
    embed.add_field(name=f"**{card[1]}**", value=f"**Rarity:** {card[2]}\n**Value:** {card[3]} 🪙\n**Quantity Remaining:** {quantity}", inline=False)
    embed.set_image(url=card[4])
    await interaction.channel.send(embed=embed, view=DropView(card, quantity))
    await interaction.response.send_message("Drop sent!", ephemeral=True)

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
    if not interaction.user.guild_permissions.manage_guild: return await interaction.followup.send("❌ Admin!")
    cursor.execute('SELECT * FROM cards')
    cards = cursor.fetchall()
    sorted_cards = sorted(cards, key=lambda x: RARITY_ORDER.get(x[2], 99))
    view = CardPaginator(sorted_cards, 0, "Global List")
    await interaction.followup.send(embed=view.create_embed(), view=view)

# --- MARKET SYSTEM COMMANDS ---

@client.tree.command(name="market_sell", description="Sell a card in the global market")
async def market_sell(interaction: discord.Interaction, name: str, amount: int, quantity: int):
    await interaction.response.defer(ephemeral=True)
    
    # Check if user owns the card and quantity
    cursor.execute('''SELECT c.card_id, i.quantity, c.name FROM inventory i 
                      JOIN cards c ON i.card_id = c.card_id 
                      WHERE i.user_id = ? AND (c.name = ? OR c.card_id = ?)''', (str(interaction.user.id), name, name))
    card = cursor.fetchone()

    if not card:
        err_embed = discord.Embed(description=f"{interaction.user.mention} ⚠️ you don't have that card in your inventory!", color=discord.Color.red())
        return await interaction.followup.send(embed=err_embed)
    
    if card[1] < quantity:
        err_embed = discord.Embed(description=f"{interaction.user.mention} ⚠️ you don't have that much card in your inventory!", color=discord.Color.red())
        return await interaction.followup.send(embed=err_embed)

    # Generate unique 6-digit selling ID
    while True:
        selling_id = random.randint(100000, 999999)
        cursor.execute('SELECT 1 FROM market WHERE selling_id = ?', (selling_id,))
        if not cursor.fetchone(): break

    # Remove cards from inventory and add to market
    cursor.execute('UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND card_id = ?', (quantity, str(interaction.user.id), card[0]))
    cursor.execute('DELETE FROM inventory WHERE quantity <= 0')
    cursor.execute('INSERT INTO market (selling_id, seller_id, card_id, price, quantity) VALUES (?, ?, ?, ?, ?)', (selling_id, str(interaction.user.id), card[0], amount, quantity))
    conn.commit()

    success_embed = discord.Embed(description=f"✅ Successfully listed **{quantity}x {card[2]}** on the market for **{amount} 🪙** each!\nSelling ID: `{selling_id}`", color=discord.Color.green())
    await interaction.followup.send(embed=success_embed)

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

@client.tree.command(name="set_channel", description="Admin: Set the default channel for bot announcements")
async def set_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not interaction.user.guild_permissions.manage_guild: 
        return await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
    
    # Save the channel ID to the config table
    cursor.execute('''INSERT INTO config (key, value) VALUES (?, ?) 
                      ON CONFLICT(key) DO UPDATE SET value = ?''', 
                   ('default_channel', str(channel.id), str(channel.id)))
    conn.commit()
    
    embed = discord.Embed(description=f"✅ Default announcement channel successfully set to {channel.mention}", color=discord.Color.green())
    await interaction.response.send_message(embed=embed, ephemeral=True)


@client.tree.command(name="clear_balance", description="Admin: Reset a user's coin balance to 0")
async def clear_balance(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.guild_permissions.manage_guild: 
        return await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
    
    cursor.execute('UPDATE users SET balance = 0 WHERE id = ?', (str(user.id),))
    conn.commit()
    
    embed = discord.Embed(description=f"✅ Successfully cleared {user.mention}'s coin balance to 0 🪙.", color=discord.Color.green())
    await interaction.response.send_message(embed=embed, ephemeral=True)


@client.tree.command(name="clear_inventory", description="Admin: Remove all cards from a user's inventory")
async def clear_inventory(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.guild_permissions.manage_guild: 
        return await interaction.response.send_message("❌ You do not have permission to use this command.", ephemeral=True)
    
    cursor.execute('DELETE FROM inventory WHERE user_id = ?', (str(user.id),))
    conn.commit()
    
    embed = discord.Embed(description=f"✅ Successfully emptied {user.mention}'s card inventory.", color=discord.Color.green())
    await interaction.response.send_message(embed=embed, ephemeral=True)
    

# --- PART 8: ADMIN MANAGEMENT COMMANDS ---

@client.tree.command(name="delete_card", description="Admin: Delete a card completely from the game")
async def delete_card(interaction: discord.Interaction, card_name: str):
    if not interaction.user.guild_permissions.manage_guild: 
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    
    cursor.execute('SELECT card_id, name FROM cards WHERE name = ? OR card_id = ?', (card_name, card_name))
    card = cursor.fetchone()
    if not card: 
        return await interaction.response.send_message("❌ Card not found.", ephemeral=True)
    
    card_id, real_name = card[0], card[1]
    
    # Delete from everywhere so it doesn't break inventories or the market
    cursor.execute('DELETE FROM cards WHERE card_id = ?', (card_id,))
    cursor.execute('DELETE FROM inventory WHERE card_id = ?', (card_id,))
    cursor.execute('DELETE FROM market WHERE card_id = ?', (card_id,))
    conn.commit()
    
    await interaction.response.send_message(f"✅ Card **{real_name}** has been permanently deleted from the database, all inventories, and the market.", ephemeral=True)

@client.tree.command(name="remove_coin", description="Admin: Remove coins from a user")
async def remove_coin(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not interaction.user.guild_permissions.manage_guild: 
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    
    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(user.id),))
    row = cursor.fetchone()
    balance = row[0] if row else 0
    
    if balance < amount:
        err_embed = discord.Embed(description=f"{user.mention} doesn't have enough coin to remove.\n**Balance:** {balance} 🪙", color=discord.Color.red())
        return await interaction.response.send_message(embed=err_embed)
    
    cursor.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (amount, str(user.id)))
    conn.commit()
    
    await interaction.response.send_message(f"✅ Successfully removed {amount} 🪙 from {user.mention}.", ephemeral=True)

@client.tree.command(name="remove_card", description="Admin: Remove specific cards from a user")
async def remove_card(interaction: discord.Interaction, user: discord.Member, card_name: str, quantity: int):
    if not interaction.user.guild_permissions.manage_guild: 
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    
    cursor.execute('''SELECT c.card_id, i.quantity, c.name FROM inventory i 
                      JOIN cards c ON i.card_id = c.card_id 
                      WHERE i.user_id = ? AND (c.name = ? OR c.card_id = ?)''', 
                   (str(user.id), card_name, card_name))
    card = cursor.fetchone()
    
    # Note: I used Color.red() here for errors. Change to Color.green() if you prefer!
    if not card:
        embed = discord.Embed(description=f"{user.mention} doesn't have that card to remove.", color=discord.Color.red())
        return await interaction.response.send_message(embed=embed)
        
    if card[1] < quantity:
        embed = discord.Embed(description=f"{user.mention} doesn't have enough card to remove.", color=discord.Color.red())
        return await interaction.response.send_message(embed=embed)
        
    cursor.execute('UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND card_id = ?', (quantity, str(user.id), card[0]))
    cursor.execute('DELETE FROM inventory WHERE quantity <= 0') # Clean up 0 quantity rows
    conn.commit()
    
    await interaction.response.send_message(f"✅ Removed {quantity}x **{card[2]}** from {user.mention}'s inventory.", ephemeral=True)

@client.tree.command(name="remove_rarity", description="Admin: Remove a rarity tier")
async def remove_rarity(interaction: discord.Interaction, rarity: str):
    if not interaction.user.guild_permissions.manage_guild: 
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    
    cursor.execute('DELETE FROM rarities WHERE name = ?', (rarity,))
    if cursor.rowcount == 0:
        return await interaction.response.send_message(f"❌ Rarity **{rarity}** not found.", ephemeral=True)
        
    # Change affected cards to "Unknown"
    cursor.execute('UPDATE cards SET rarity = "Unknown" WHERE rarity = ?', (rarity,))
    conn.commit()
    
    await interaction.response.send_message(f"✅ Rarity **{rarity}** removed. Any affected cards now have 'Unknown' rarity.", ephemeral=True)

@client.tree.command(name="edit", description="Admin: Edit an existing card's details")
async def edit(interaction: discord.Interaction, card_name: str, new_name: str = None, rarity: str = None, value: int = None, image: str = None):
    if not interaction.user.guild_permissions.manage_guild: 
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    
    cursor.execute('SELECT card_id, name, rarity, value, image FROM cards WHERE name = ? OR card_id = ?', (card_name, card_name))
    card = cursor.fetchone()
    
    if not card: 
        return await interaction.response.send_message("❌ Card not found.", ephemeral=True)
    
    card_id = card[0]
    
    # Keep the old values if the user didn't provide new ones
    final_name = new_name if new_name else card[1]
    final_rarity = rarity if rarity else card[2]
    final_value = value if value is not None else card[3]
    final_image = image if image else card[4]
    
    try:
        cursor.execute('UPDATE cards SET name = ?, rarity = ?, value = ?, image = ? WHERE card_id = ?', 
                       (final_name, final_rarity, final_value, final_image, card_id))
        conn.commit()
        await interaction.response.send_message(f"✅ Card **{card[1]}** updated successfully!", ephemeral=True)
    except sqlite3.IntegrityError:
        # This triggers if they try to rename it to a name that already exists
        await interaction.response.send_message("❌ A card with that new name already exists!", ephemeral=True)
    


if __name__ == '__main__':
    Thread(target=run_flask).start()
    client.run(os.environ.get('DISCORD_TOKEN'))

    
