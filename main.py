import discord
from discord import app_commands, ui
import sqlite3
import os
import random
from flask import Flask
from threading import Thread
import datetime
import asyncio
import urllib.request
import urllib.parse
from PIL import Image, ImageDraw, ImageFont
import io

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


    cursor.execute('CREATE TABLE IF NOT EXISTS anime (name TEXT)')

    cursor.execute("PRAGMA table_info(cards)")
    cards_columns = [column[1] for column in cursor.fetchall()]
    if "anime" not in cards_columns:
        cursor.execute("ALTER TABLE cards ADD COLUMN anime TEXT")


    cursor.execute('CREATE TABLE IF NOT EXISTS Crate (crate_id TEXT PRIMARY KEY, crate_name TEXT, crate_type TEXT, value TEXT)')
    cursor.execute('''CREATE TABLE IF NOT EXISTS crate_inventory (
                        user_id TEXT,
                        crate_id TEXT,
                        quantity INTEGER DEFAULT 1,
                        UNIQUE(user_id, crate_id))''')

    cursor.execute('''CREATE TABLE IF NOT EXISTS Level (
                        server_id TEXT,
                        user_id TEXT,
                        level INTEGER DEFAULT 0,
                        xp INTEGER DEFAULT 0,
                        UNIQUE(server_id, user_id))''')

    

    # 2. AUTO-REPAIR: Ensure columns exist in the cloud
    cursor.execute("PRAGMA table_info(users)")
    existing_columns = [column[1] for column in cursor.fetchall()]
    
    if "account_status" not in existing_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN account_status TEXT DEFAULT 'public'")
    if "last_beg" not in existing_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_beg TIMESTAMP")
    if "last_daily" not in existing_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN last_daily TIMESTAMP")
    if "web_id" not in existing_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN web_id TEXT")
    if "web_password" not in existing_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN web_password TEXT")
    if "username" not in existing_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN username TEXT")
    if "pfp" not in existing_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN pfp TEXT")
    
    

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
        super().__init__(timeout=None)
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




class CrateSelect(discord.ui.Select):
    def __init__(self, owner_id, crates):
        options = [discord.SelectOption(label=f"{name} - {qty}×", value=crate_id) for crate_id, name, qty in crates]
        super().__init__(placeholder="Select Crate", min_values=1, max_values=1, options=options)
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("❌ This isn't your crate menu.", ephemeral=True)

        crate_id = self.values[0]
        local_cursor = conn.cursor()

        local_cursor.execute("SELECT quantity FROM crate_inventory WHERE user_id = ? AND crate_id = ?", (str(interaction.user.id), crate_id))
        row = local_cursor.fetchone()
        if not row or row[0] <= 0:
            return await interaction.response.send_message("❌ You no longer have that crate.", ephemeral=True)

        local_cursor.execute("SELECT crate_name, crate_type, value FROM Crate WHERE crate_id = ?", (crate_id,))
        crate = local_cursor.fetchone()
        if not crate:
            return await interaction.response.send_message("❌ That crate no longer exists.", ephemeral=True)
        crate_name, crate_type, value = crate

        if row[0] - 1 <= 0:
            local_cursor.execute("DELETE FROM crate_inventory WHERE user_id = ? AND crate_id = ?", (str(interaction.user.id), crate_id))
        else:
            local_cursor.execute("UPDATE crate_inventory SET quantity = quantity - 1 WHERE user_id = ? AND crate_id = ?", (str(interaction.user.id), crate_id))
        conn.commit()

        if crate_type == "Coins":
            amount = int(value)
            local_cursor.execute("INSERT INTO users (id, balance) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET balance = balance + ?", (str(interaction.user.id), amount, amount))
            conn.commit()
            local_cursor.execute("SELECT balance FROM users WHERE id = ?", (str(interaction.user.id),))
            new_balance = local_cursor.fetchone()[0]

            embed = discord.Embed(
                description=f"<:crate:1537797263714295819> **{interaction.user.display_name}** opened **{crate_name}** and received **{amount}** 🪙.\n**Balance:** {new_balance} 🪙.",
                color=discord.Color.gold()
            )
            await interaction.response.edit_message(embed=embed, view=None)

        else:
            local_cursor.execute("SELECT card_id, name, rarity, value, image, anime FROM cards WHERE name = ?", (value,))
            card = local_cursor.fetchone()
            if not card:
                embed = discord.Embed(description=f"❌ Crate reward card **{value}** no longer exists. Contact an admin.", color=discord.Color.red())
                return await interaction.response.edit_message(embed=embed, view=None)

            card_id, card_name, rarity, card_value, image, anime = card
            local_cursor.execute("INSERT INTO inventory (user_id, card_id, quantity) VALUES (?, ?, 1) ON CONFLICT(user_id, card_id) DO UPDATE SET quantity = quantity + 1", (str(interaction.user.id), card_id))
            conn.commit()
            local_cursor.execute("SELECT quantity FROM inventory WHERE user_id = ? AND card_id = ?", (str(interaction.user.id), card_id))
            total_qty = local_cursor.fetchone()[0]

            local_cursor.execute("SELECT color FROM rarities WHERE name = ?", (rarity,))
            color_row = local_cursor.fetchone()
            embed_color = discord.Color(int(color_row[0].lstrip("#"), 16)) if color_row and color_row[0] else discord.Color.default()

            embed = discord.Embed(
                description=(
                    f"<:crate:1537797263714295819> **{interaction.user.display_name}** opened **{crate_name}** and received **{card_name}**\n\n"
                    f"**{card_name}**\nRarity: {rarity}\nValue: {card_value}\nAnime: {anime or 'Unknown'}\n"
                    f"Card ID: {card_id}\nQuantity: {total_qty}"
                ),
                color=embed_color
            )
            if image:
                embed.set_image(url=image)
            await interaction.response.edit_message(embed=embed, view=None)


class CrateOpenView(discord.ui.View):
    def __init__(self, owner_id, crates):
        super().__init__(timeout=120)
        self.add_item(CrateSelect(owner_id, crates))
        


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
        super().__init__(timeout=None)
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
        super().__init__(timeout=None)
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
        super().__init__(timeout=None)
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
        
        local_cursor = conn.cursor()
        local_cursor.execute("SELECT balance FROM users WHERE id = ?", (str(self.user.id),))
        row = local_cursor.fetchone()
        bal = row[0] if row else 0
        embed.set_footer(text=f"Balance: {bal} 🪙")
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

    await client.change_presence(
    status=discord.Status.online,
    activity=discord.Activity(type=discord.ActivityType.playing, name="/help")
    )


@client.event
async def on_message(message):
    if message.author.bot: return
    c = random.randint(10, 200)
    local_cursor = conn.cursor()
    local_cursor.execute('INSERT INTO users (id, balance) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET balance = balance + ?', (str(message.author.id), c, c))
    conn.commit()
    await route_prefix_command(message)



#CARD CHOICE 
async def card_name_autocomplete(interaction: discord.Interaction, current: str):
    local_cursor = conn.cursor()
    local_cursor.execute("SELECT name FROM cards WHERE name LIKE ? ORDER BY name LIMIT 25", (f"%{current}%",))
    rows = local_cursor.fetchall()
    return [app_commands.Choice(name=row[0], value=row[0]) for row in rows]


async def owned_card_autocomplete(interaction: discord.Interaction, current: str):
    user_id = str(interaction.user.id)
    local_cursor = conn.cursor()
    local_cursor.execute("""
        SELECT c.name FROM cards c
        JOIN inventory i ON c.card_id = i.card_id
        WHERE i.user_id = ? AND c.name LIKE ?
        ORDER BY c.name LIMIT 25
    """, (user_id, f"%{current}%"))
    rows = local_cursor.fetchall()
    return [app_commands.Choice(name=row[0], value=row[0]) for row in rows]


async def own_listing_autocomplete(interaction: discord.Interaction, current: str):
    user_id = str(interaction.user.id)
    local_cursor = conn.cursor()
    local_cursor.execute("""
        SELECT m.selling_id, c.name, m.price, m.quantity FROM market m
        JOIN cards c ON m.card_id = c.card_id
        WHERE m.seller_id = ?
        ORDER BY c.name LIMIT 25
    """, (user_id,))
    rows = local_cursor.fetchall()
    return [
        app_commands.Choice(name=f"{name} — {price}🪙 x{qty}", value=selling_id)
        for selling_id, name, price, qty in rows
        if current.lower() in name.lower()
    ]

async def rarity_autocomplete(interaction: discord.Interaction, current: str):
    local_cursor = conn.cursor()
    local_cursor.execute("SELECT name FROM rarities WHERE name LIKE ? ORDER BY name LIMIT 25", (f"%{current}%",))
    return [app_commands.Choice(name=row[0], value=row[0]) for row in local_cursor.fetchall()]


async def anime_autocomplete(interaction: discord.Interaction, current: str):
    local_cursor = conn.cursor()
    local_cursor.execute("SELECT name FROM anime WHERE name LIKE ? ORDER BY name LIMIT 25", (f"%{current}%",))
    return [app_commands.Choice(name=row[0], value=row[0]) for row in local_cursor.fetchall()]


#LEVELING SYSTEM

FONT_PATH = "assets/font.ttf"

RARITY_XP = {
    "Common": 10, "Uncommon": 25, "Rare": 60,
    "Epic": 150, "Legendary": 400, "Super Legendary": 1000
}

def xp_required_for_level(level, base=100, exp=1.6):
    return 0 if level <= 0 else round(base * (level ** exp))

def _load_font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except Exception:
        return ImageFont.load_default()


TITLE_FONT_PATH = "assets/title_font.ttf"
COIN_ICON_PATH = "assets/coin.png"

def _load_title_font(size):
    try:
        return ImageFont.truetype(TITLE_FONT_PATH, size)
    except Exception:
        return _load_font(size)

def _load_coin_icon(size):
    try:
        return Image.open(COIN_ICON_PATH).convert("RGBA").resize((size, size))
    except Exception:
        return None


def _circle_avatar(avatar_bytes, size):
    avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    avatar.putalpha(mask)
    return avatar

def generate_levelup_image(avatar_bytes, level, xp, balance):
    W, H = 800, 300
    canvas = Image.new("RGB", (W, H), (30, 30, 40))
    draw = ImageDraw.Draw(canvas)
    avatar = _circle_avatar(avatar_bytes, 180)
    canvas.paste(avatar, (40, (H - 180)//2), avatar)

    title_font, label_font = _load_font(48), _load_font(28)
    tw = draw.textlength("LEVEL UP", font=title_font)
    draw.text(((W - tw)/2, 30), "LEVEL UP", font=title_font, fill=(255, 215, 0))

    x = 260
    draw.text((x, 120), f"Level: {level}", font=label_font, fill=(255, 255, 255))
    draw.text((x, 165), f"Xp: {xp}", font=label_font, fill=(255, 255, 255))
    draw.text((x, 210), f"Balance: {balance} 🪙", font=label_font, fill=(255, 255, 255))

    buf = io.BytesIO(); canvas.save(buf, format="PNG"); buf.seek(0)
    return buf

def generate_level_image(avatar_bytes, level, current_xp, next_level_xp):
    W, H = 800, 300
    canvas = Image.new("RGB", (W, H), (30, 30, 40))
    draw = ImageDraw.Draw(canvas)
    avatar = _circle_avatar(avatar_bytes, 180)
    canvas.paste(avatar, (40, (H - 180)//2), avatar)

    title_font, level_font, small_font = _load_font(40), _load_font(34), _load_font(22)
    tw = draw.textlength("LEVEL", font=title_font)
    draw.text(((W - tw)/2, 25), "LEVEL", font=title_font, fill=(255, 215, 0))
    lvl_text = f"Level {level}"
    lw = draw.textlength(lvl_text, font=level_font)
    draw.text(((W - lw)/2, 80), lvl_text, font=level_font, fill=(255, 255, 255))

    bar_x, bar_y, bar_w, bar_h = 260, 180, 470, 30
    progress = 0 if next_level_xp == 0 else min(1.0, current_xp / next_level_xp)
    draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), radius=15, fill=(60, 60, 70))
    draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w * progress, bar_y + bar_h), radius=15, fill=(88, 220, 120))

    draw.text((bar_x, bar_y + bar_h + 8), str(current_xp), font=small_font, fill=(255, 255, 255))
    nt = str(next_level_xp)
    nw = draw.textlength(nt, font=small_font)
    draw.text((bar_x + bar_w - nw, bar_y + bar_h + 8), nt, font=small_font, fill=(255, 255, 255))

    buf = io.BytesIO(); canvas.save(buf, format="PNG"); buf.seek(0)
    return buf




async def award_xp(guild_id, member: discord.Member, rarities: list, channel: discord.abc.Messageable):
    xp_gained = sum(RARITY_XP.get(r, 0) for r in rarities)
    if xp_gained <= 0:
        return

    local_cursor = conn.cursor()
    local_cursor.execute(
        "INSERT INTO Level (server_id, user_id, level, xp) VALUES (?, ?, 0, ?) "
        "ON CONFLICT(server_id, user_id) DO UPDATE SET xp = xp + ?",
        (str(guild_id), str(member.id), xp_gained, xp_gained)
    )
    conn.commit()

    local_cursor.execute("SELECT level, xp FROM Level WHERE server_id = ? AND user_id = ?", (str(guild_id), str(member.id)))
    level, total_xp = local_cursor.fetchone()

    leveled_up = False
    while total_xp >= xp_required_for_level(level + 1):
        level += 1
        leveled_up = True

    if leveled_up:
        local_cursor.execute("UPDATE Level SET level = ? WHERE server_id = ? AND user_id = ?", (level, str(guild_id), str(member.id)))
        conn.commit()
        local_cursor.execute("SELECT balance FROM users WHERE id = ?", (str(member.id),))
        row = local_cursor.fetchone()
        balance = row[0] if row else 0

        avatar_bytes = await member.display_avatar.read()
        img = await asyncio.to_thread(generate_levelup_image, avatar_bytes, level, total_xp, balance)
        await channel.send(
            content=f"{member.mention} reached level **{level}**. Congratulations. We are proud of you!!",
            file=discord.File(fp=img, filename="levelup.png")
        )




def generate_leaderboard_image(title, header_label, rows):
    row_h = 60
    header_h = 100
    col_no_w = 80
    col_name_w = 380
    col_value_w = 260
    W = col_no_w + col_name_w + col_value_w
    H = header_h + row_h * len(rows)

    canvas = Image.new("RGB", (W, H), (30, 30, 40))
    draw = ImageDraw.Draw(canvas)

    title_font = _load_title_font(36)
    header_font = _load_font(24)
    row_font = _load_font(22)

    tw = draw.textlength(title, font=title_font)
    draw.text(((W - tw) / 2, 20), title, font=title_font, fill=(255, 215, 0))

    header_y = 68
    draw.text((25, header_y), "No", font=header_font, fill=(200, 200, 200))
    draw.text((col_no_w + 20, header_y), "Name", font=header_font, fill=(200, 200, 200))
    draw.text((col_no_w + col_name_w + 20, header_y), header_label, font=header_font, fill=(200, 200, 200))

    draw.line((0, header_h, W, header_h), fill=(90, 90, 100), width=2)
    draw.line((col_no_w, header_h, col_no_w, H), fill=(90, 90, 100), width=2)
    draw.line((col_no_w + col_name_w, header_h, col_no_w + col_name_w, H), fill=(90, 90, 100), width=2)

    y = header_h
    avatar_size = 40
    coin_icon = _load_coin_icon(22)

    for rank, name, avatar_bytes, number_str, suffix in rows:
        draw.line((0, y, W, y), fill=(70, 70, 80), width=1)
        draw.text((25, y + row_h // 2 - 12), str(rank), font=row_font, fill=(255, 255, 255))

        ay = y + (row_h - avatar_size) // 2
        if avatar_bytes:
            avatar = _circle_avatar(avatar_bytes, avatar_size)
            canvas.paste(avatar, (col_no_w + 15, ay), avatar)
        else:
            draw.ellipse((col_no_w + 15, ay, col_no_w + 15 + avatar_size, ay + avatar_size), fill=(90, 90, 100))

        draw.text((col_no_w + 15 + avatar_size + 12, y + row_h // 2 - 12), name, font=row_font, fill=(255, 255, 255))

        value_x = col_no_w + col_name_w + 20
        value_y = y + row_h // 2 - 12
        draw.text((value_x, value_y), number_str, font=row_font, fill=(255, 255, 255))
        num_w = draw.textlength(number_str, font=row_font)

        if suffix == "🪙" and coin_icon:
            canvas.paste(coin_icon, (int(value_x + num_w + 8), y + row_h // 2 - 11), coin_icon)
        else:
            draw.text((value_x + num_w + 8, value_y), suffix, font=row_font, fill=(255, 255, 255))

        y += row_h

    draw.line((0, H - 1, W, H - 1), fill=(90, 90, 100), width=2)
    buf = io.BytesIO(); canvas.save(buf, format="PNG"); buf.seek(0)
    return buf


async def build_leaderboard_rows(type_value, guild_id):
    local_cursor = conn.cursor()

    if type_value == "balance":
        local_cursor.execute('SELECT id, balance FROM users ORDER BY balance DESC LIMIT 10')
        source = [(uid, str(bal), "🪙") for uid, bal in local_cursor.fetchall()]
        title, header_label = "Balance Leaderboard", "Balance"
    elif type_value == "card":
        data = get_all_leaderboard_data()[:10]
        source = [(entry["id"], str(entry["points"]), "pts") for entry in data]
        title, header_label = "Card Leaderboard", "Points"
    else:
        local_cursor.execute('SELECT user_id, level FROM Level WHERE server_id = ? ORDER BY level DESC, xp DESC LIMIT 10', (str(guild_id),))
        source = [(uid, str(lvl), "Lv") for uid, lvl in local_cursor.fetchall()]
        title, header_label = "Level Leaderboard", "Level"

    rows_data = []
    for i, (uid, number_str, suffix) in enumerate(source, 1):
        try:
            user_obj = client.get_user(int(uid)) or await client.fetch_user(int(uid))
            name = user_obj.name
            avatar_bytes = await user_obj.display_avatar.read()
        except Exception:
            name, avatar_bytes = "Unknown User", None
        rows_data.append((i, name, avatar_bytes, number_str, suffix))

    return rows_data, title, header_label



class LeaderboardView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=180)
        self.guild_id = guild_id

    @discord.ui.select(
        placeholder="Type",
        options=[
            discord.SelectOption(label="Balance", value="balance"),
            discord.SelectOption(label="Card", value="card"),
            discord.SelectOption(label="Level", value="level"),
        ]
    )
    async def select_type(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.defer()
        rows_data, title, header_label = await build_leaderboard_rows(select.values[0], self.guild_id)
        if not rows_data:
            return await interaction.followup.send("No data yet for this leaderboard.", ephemeral=True)
        img = await asyncio.to_thread(generate_leaderboard_image, title, header_label, rows_data)
        await interaction.edit_original_response(attachments=[discord.File(fp=img, filename="leaderboard.png")])


# --- 6. COMMANDS ---


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

@client.tree.command(name="inventory", description="View a collection with sorting and filters")
@app_commands.describe(
    sort="How to sort the list",
    user="Whose inventory to view (default: yourself)",
    rarity="Filter by rarity",
    anime="Filter by anime",
    minimum_value="Only show cards worth at least this much",
    maximum_value="Only show cards worth at most this much"
)
@app_commands.choices(sort=[
    app_commands.Choice(name="Low to High Value", value="value_asc"),
    app_commands.Choice(name="High to Low Value", value="value_desc"),
    app_commands.Choice(name="Low to High ID", value="id_asc"),
    app_commands.Choice(name="High to Low ID", value="id_desc"),
])
@app_commands.autocomplete(rarity=rarity_autocomplete, anime=anime_autocomplete)
async def inventory(
    interaction: discord.Interaction,
    sort: app_commands.Choice[str],
    user: discord.Member = None,
    rarity: str = None,
    anime: str = None,
    minimum_value: int = None,
    maximum_value: int = None
):
    await interaction.response.defer()
    target = user or interaction.user
    local_cursor = conn.cursor()

    if target.id != interaction.user.id:
        local_cursor.execute("SELECT account_status FROM users WHERE id = ?", (str(target.id),))
        row = local_cursor.fetchone()
        status = row[0] if row else "public"
        if status == "private":
            embed = discord.Embed(description=f"❌ {target.mention}'s profile is private. You can't check the inventory.", color=discord.Color.red())
            return await interaction.followup.send(embed=embed, ephemeral=True)

    if rarity is not None:
        local_cursor.execute("SELECT 1 FROM rarities WHERE name = ?", (rarity,))
        if not local_cursor.fetchone():
            return await interaction.followup.send(embed=discord.Embed(description=f"❌ **{rarity}** is not a valid rarity.", color=discord.Color.red()))

    if anime is not None:
        local_cursor.execute("SELECT 1 FROM anime WHERE name = ?", (anime,))
        if not local_cursor.fetchone():
            return await interaction.followup.send(embed=discord.Embed(description=f"❌ **{anime}** is not a valid anime.", color=discord.Color.red()))

    if minimum_value is not None and maximum_value is not None and minimum_value > maximum_value:
        return await interaction.followup.send(embed=discord.Embed(description="❌ Minimum value can't be greater than maximum value.", color=discord.Color.red()))

    query = '''SELECT c.card_id, c.name, c.rarity, c.value, c.image, i.quantity FROM inventory i
               JOIN cards c ON i.card_id = c.card_id WHERE i.user_id = ?'''
    params = [str(target.id)]
    if rarity is not None:
        query += " AND c.rarity = ?"; params.append(rarity)
    if anime is not None:
        query += " AND c.anime = ?"; params.append(anime)
    if minimum_value is not None:
        query += " AND c.value >= ?"; params.append(minimum_value)
    if maximum_value is not None:
        query += " AND c.value <= ?"; params.append(maximum_value)

    sort_map = {
        "value_asc": " ORDER BY c.value ASC",
        "value_desc": " ORDER BY c.value DESC",
        "id_asc": " ORDER BY CAST(c.card_id AS INTEGER) ASC",
        "id_desc": " ORDER BY CAST(c.card_id AS INTEGER) DESC",
    }
    query += sort_map[sort.value]

    local_cursor.execute(query, params)
    items = local_cursor.fetchall()

    if not items:
        return await interaction.followup.send(embed=discord.Embed(description="⚠️ No cards match those filters.", color=discord.Color.orange()))

    title = "Your Collection" if target.id == interaction.user.id else f"{target.name}'s Collection"
    view = CardPaginator(items, 0, title)
    await interaction.followup.send(embed=view.create_embed(), view=view)
# --- 1. /addcoin (Admin) ---

# --- 2. /view_card (Member) ---
@app_commands.autocomplete(query=card_name_autocomplete)
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
    await award_xp(interaction.guild.id, interaction.user, [chosen_rarity], interaction.channel)


# --- 6. COMMANDS (REPLACEMENTS) ---


@client.tree.command(name="trade", description="Sell cards for coins via DM")
async def trade(interaction: discord.Interaction, user: discord.Member, card_name_or_id: str, trade_amount: int, quantity: int):
    await interaction.response.defer(ephemeral=True)
    cursor.execute('SELECT c.card_id, c.name, c.rarity, c.value, c.image, i.quantity FROM inventory i JOIN cards c ON i.card_id = c.card_id WHERE i.user_id = ? AND (c.name = ? OR c.card_id = ?)', (str(interaction.user.id), card_name_or_id, card_name_or_id))
    card = cursor.fetchone()
    if not card or card[5] < quantity: return await interaction.followup.send("❌ You don't have enough copies!")
    embed = discord.Embed(title="🤝 Trade Offer", color=discord.Color.blue())
    embed.add_field(name="Details", value=f"**Seller:** {interaction.user.name}\n**Card:** {card[1]}\n**Qty:** {quantity}\n**Total:** {trade_amount * quantity} 🪙")
    embed.set_image(url=card[4])
    try:
        await user.send(embed=embed, view=SaleView(interaction.user, user, card, trade_amount, quantity))
        await interaction.followup.send(f"✅ Offer sent to {user.name}!")
    except: await interaction.followup.send("❌ User has DMs closed!")

@client.tree.command(name="card_list", description="Browse all cards with sorting and filters")
@app_commands.describe(
    sort="How to sort the list",
    rarity="Filter by rarity",
    anime="Filter by anime",
    minimum_value="Only show cards worth at least this much",
    maximum_value="Only show cards worth at most this much"
)
@app_commands.choices(sort=[
    app_commands.Choice(name="Low to High Value", value="value_asc"),
    app_commands.Choice(name="High to Low Value", value="value_desc"),
    app_commands.Choice(name="Low to High ID", value="id_asc"),
    app_commands.Choice(name="High to Low ID", value="id_desc"),
])
@app_commands.autocomplete(rarity=rarity_autocomplete, anime=anime_autocomplete)
async def card_list(
    interaction: discord.Interaction,
    sort: app_commands.Choice[str],
    rarity: str = None,
    anime: str = None,
    minimum_value: int = None,
    maximum_value: int = None
):
    await interaction.response.defer()
    local_cursor = conn.cursor()

    if rarity is not None:
        local_cursor.execute("SELECT 1 FROM rarities WHERE name = ?", (rarity,))
        if not local_cursor.fetchone():
            embed = discord.Embed(description=f"❌ **{rarity}** is not a valid rarity.", color=discord.Color.red())
            return await interaction.followup.send(embed=embed)

    if anime is not None:
        local_cursor.execute("SELECT 1 FROM anime WHERE name = ?", (anime,))
        if not local_cursor.fetchone():
            embed = discord.Embed(description=f"❌ **{anime}** is not a valid anime.", color=discord.Color.red())
            return await interaction.followup.send(embed=embed)

    if minimum_value is not None and maximum_value is not None and minimum_value > maximum_value:
        embed = discord.Embed(description="❌ Minimum value can't be greater than maximum value.", color=discord.Color.red())
        return await interaction.followup.send(embed=embed)

    query = "SELECT card_id, name, rarity, value, image, anime FROM cards WHERE 1=1"
    params = []
    if rarity is not None:
        query += " AND rarity = ?"; params.append(rarity)
    if anime is not None:
        query += " AND anime = ?"; params.append(anime)
    if minimum_value is not None:
        query += " AND value >= ?"; params.append(minimum_value)
    if maximum_value is not None:
        query += " AND value <= ?"; params.append(maximum_value)

    sort_map = {
        "value_asc": " ORDER BY value ASC",
        "value_desc": " ORDER BY value DESC",
        "id_asc": " ORDER BY CAST(card_id AS INTEGER) ASC",
        "id_desc": " ORDER BY CAST(card_id AS INTEGER) DESC",
    }
    query += sort_map[sort.value]

    local_cursor.execute(query, params)
    cards = local_cursor.fetchall()

    if not cards:
        embed = discord.Embed(description="⚠️ No cards match those filters.", color=discord.Color.orange())
        return await interaction.followup.send(embed=embed)

    view = CardPaginator(cards, 0, "Card List")
    await interaction.followup.send(embed=view.create_embed(), view=view)

@client.tree.command(name="burn", description="Burn your cards to receive 50% of their value in coins")
@app_commands.describe(card_name="Name or ID of the card to burn", quantity="How many to burn")
@app_commands.autocomplete(card_name=owned_card_autocomplete)
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
    


@client.tree.command(name="bulk_burn", description="Burn multiple cards at once")
@app_commands.choices(filter=[
    app_commands.Choice(name="Burn all cards", value="all"),
    app_commands.Choice(name="Burn all duplicate cards", value="duplicates"),
])
@app_commands.autocomplete(rarity=rarity_autocomplete, anime=anime_autocomplete)
async def bulk_burn(interaction: discord.Interaction, filter: app_commands.Choice[str], rarity: str = None, anime: str = None):
    await interaction.response.defer()
    local_cursor = conn.cursor()

    if rarity is not None:
        local_cursor.execute("SELECT 1 FROM rarities WHERE name = ?", (rarity,))
        if not local_cursor.fetchone():
            return await interaction.followup.send(embed=discord.Embed(description=f"❌ **{rarity}** is not a valid rarity.", color=discord.Color.red()))

    if anime is not None:
        local_cursor.execute("SELECT 1 FROM anime WHERE name = ?", (anime,))
        if not local_cursor.fetchone():
            return await interaction.followup.send(embed=discord.Embed(description=f"❌ **{anime}** is not a valid anime.", color=discord.Color.red()))

    query = '''SELECT c.card_id, c.rarity, c.value, i.quantity FROM inventory i
               JOIN cards c ON i.card_id = c.card_id WHERE i.user_id = ?'''
    params = [str(interaction.user.id)]
    if rarity is not None:
        query += " AND c.rarity = ?"; params.append(rarity)
    if anime is not None:
        query += " AND c.anime = ?"; params.append(anime)

    local_cursor.execute(query, params)
    owned = local_cursor.fetchall()

    rarity_counts = {"Common": 0, "Uncommon": 0, "Rare": 0, "Epic": 0, "Legendary": 0, "Super Legendary": 0}
    total_coins = 0
    total_burned = 0

    for card_id, card_rarity, value, quantity in owned:
        if filter.value == "all":
            burn_qty = quantity
        else:
            burn_qty = quantity - 1
            if burn_qty <= 0:
                continue

        coins = int(value * burn_qty * 0.5)
        total_coins += coins
        total_burned += burn_qty
        rarity_counts[card_rarity] = rarity_counts.get(card_rarity, 0) + burn_qty

        if burn_qty == quantity:
            local_cursor.execute("DELETE FROM inventory WHERE user_id = ? AND card_id = ?", (str(interaction.user.id), card_id))
        else:
            local_cursor.execute("UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND card_id = ?", (burn_qty, str(interaction.user.id), card_id))

    if total_burned == 0:
        return await interaction.followup.send(embed=discord.Embed(description="⚠️ No cards matched those filters to burn.", color=discord.Color.orange()))

    local_cursor.execute("INSERT INTO users (id, balance) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET balance = balance + ?",
                          (str(interaction.user.id), total_coins, total_coins))
    conn.commit()

    embed = discord.Embed(
        title=f"{total_burned} Cards burned.",
        description=(
            f"Common: {rarity_counts['Common']} Cards\n"
            f"Uncommon: {rarity_counts['Uncommon']} Cards\n"
            f"Rare: {rarity_counts['Rare']} Cards\n"
            f"Epic: {rarity_counts['Epic']} Cards\n"
            f"Legendary: {rarity_counts['Legendary']} Cards\n"
            f"Super Legendary: {rarity_counts['Super Legendary']} Cards\n\n"
            f"{interaction.user.mention} received **{total_coins}** 🪙."
        ),
        color=discord.Color.green()
    )
    await interaction.followup.send(embed=embed)


# --- MARKET SYSTEM COMMANDS ---
@app_commands.autocomplete(card_name=owned_card_autocomplete)
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


@app_commands.autocomplete(id=own_listing_autocomplete)
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
@app_commands.autocomplete(card_name=owned_card_autocomplete)
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


# --- PART 7: ADMIN UTILITIES ---


# --- PART 8: ADMIN MANAGEMENT COMMANDS ---

# --- PART 9: FINAL FEATURES ---
@client.tree.command(name="crate", description="Open one of your crates")
async def crate(interaction: discord.Interaction):
    await interaction.response.defer()
    local_cursor = conn.cursor()
    local_cursor.execute('''
        SELECT c.crate_id, c.crate_name, ci.quantity FROM crate_inventory ci
        JOIN Crate c ON ci.crate_id = c.crate_id
        WHERE ci.user_id = ? AND ci.quantity > 0 ORDER BY c.crate_name
    ''', (str(interaction.user.id),))
    crates = local_cursor.fetchall()

    if not crates:
        return await interaction.followup.send(embed=discord.Embed(description="❌ You don't have any crates to open.", color=discord.Color.red()))

    embed = discord.Embed(title="Open a Crate <:crate:1537797263714295819>", description="Select a crate from below to open it.", color=discord.Color.red())
    await interaction.followup.send(embed=embed, view=CrateOpenView(interaction.user.id, crates))


@client.tree.command(name="account", description="Set your account privacy")
@app_commands.choices(status=[
    app_commands.Choice(name="Public", value="public"),
    app_commands.Choice(name="Private", value="private")
])
async def account(interaction: discord.Interaction, status: app_commands.Choice[str]):
    cursor.execute('INSERT INTO users (id, account_status) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET account_status = ?', (str(interaction.user.id), status.value, status.value))
    conn.commit()
    await interaction.response.send_message(f"✅ Your account is now **{status.name}**.", ephemeral=True)


@client.tree.command(name="balance", description="Check your (or another user's) coin balance")
async def balance(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user

    if target.id != interaction.user.id:
        cursor.execute('SELECT account_status FROM users WHERE id = ?', (str(target.id),))
        row = cursor.fetchone()
        status = row[0] if row else "public"
        if status == "private":
            embed = discord.Embed(description=f"❌ {target.mention}'s profile is private. You can't check the balance.", color=discord.Color.red())
            return await interaction.response.send_message(embed=embed, ephemeral=True)

    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(target.id),))
    row = cursor.fetchone()
    bal = row[0] if row else 0
    embed = discord.Embed(title=f"{target.name}'s balance", description=f"**Balance:** {bal} 🪙", color=0xFFFF00)
    await interaction.response.send_message(embed=embed)



@client.tree.command(name="login", description="Get your Web Portal ID and Password")
async def login(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)
    username = interaction.user.name
    
    # Run the upload in a non-blocking background thread
    discord_pfp = interaction.user.display_avatar.url
    pfp = await asyncio.to_thread(upload_to_catbox_sync, discord_pfp)
    
    # Check if user already has an ID and Password
    cursor.execute("SELECT web_id, web_password FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    
    if row and row[0] and row[1]:
        web_id, web_pass = row[0], row[1]
        
        # Existing user: update username and permanent catbox PFP
        cursor.execute("""
            UPDATE users SET username = ?, pfp = ? WHERE id = ?
        """, (username, pfp, user_id))
        conn.commit()
    else:
        # New user: generate unique 10-digit ID and 4-digit PIN
        while True:
            new_id = "".join([str(random.randint(0, 9)) for _ in range(10)])
            cursor.execute("SELECT id FROM users WHERE web_id = ?", (new_id,))
            if not cursor.fetchone(): 
                break
        new_pass = "".join([str(random.randint(0, 9)) for _ in range(4)])
        
        # Insert user along with initial credentials, username, and permanent pfp
        cursor.execute("""
            INSERT INTO users (id, web_id, web_password, username, pfp) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET 
                web_id = ?, 
                web_password = ?, 
                username = ?, 
                pfp = ?
        """, (user_id, new_id, new_pass, username, pfp, new_id, new_pass, username, pfp))
        conn.commit()
        web_id, web_pass = new_id, new_pass

    WEBSITE_URL = "https://your-website-link.vercel.app"

    embed = discord.Embed(
        title="🔑 Web Portal Login Credentials",
        description="Click on the ID and Password below to copy them to your clipboard!",
        color=discord.Color.green()
    )
    embed.add_field(name="Id", value=f"`{web_id}`", inline=True)
    embed.add_field(name="Password", value=f"`{web_pass}`", inline=True)
    embed.add_field(
        name="Link", 
        value=f"[Open website]({WEBSITE_URL})", 
        inline=False
    )
    embed.set_footer(text="Keep your password private!")
    await interaction.followup.send(embed=embed, ephemeral=True)
    

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


@client.tree.command(name="cointoss", description="Bet your coins on a coin flip")
@app_commands.describe(amount="Amount of coins to bet", call="Your call")
@app_commands.choices(call=[
    app_commands.Choice(name="Head", value="Head"),
    app_commands.Choice(name="Tail", value="Tail")
])
async def cointoss(interaction: discord.Interaction, amount: int, call: app_commands.Choice[str]):
    await interaction.response.defer()

    user_id = str(interaction.user.id)
    cursor.execute('SELECT balance FROM users WHERE id = ?', (user_id,))
    row = cursor.fetchone()
    balance = row[0] if row else 0

    if amount <= 0 or amount > balance:
        embed = discord.Embed(
            description=f"❌ You have insufficient balance.\n**Your balance:** {balance} 🪙",
            color=discord.Color.red()
        )
        return await interaction.followup.send(embed=embed, ephemeral=True)

    outcome = random.choice(["Head", "Tail"])
    user_call = call.value

    if outcome == user_call:
        winnings = amount * 2
        cursor.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (winnings - amount, user_id))
        conn.commit()
        embed = discord.Embed(
            description=f"It was **{outcome}** and your call was **{user_call}**\nYou won **{winnings}** 🪙",
            color=discord.Color.green()
        )
    else:
        cursor.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (amount, user_id))
        conn.commit()
        embed = discord.Embed(
            description=f"It was **{outcome}** and your call was **{user_call}**\nYou lost **{amount}** 🪙",
            color=discord.Color.red()
        )

    await interaction.followup.send(embed=embed)

@client.tree.command(name="level", description="View your level")
async def level(interaction: discord.Interaction, user: discord.Member = None):
    await interaction.response.defer()
    target = user or interaction.user
    local_cursor = conn.cursor()
    local_cursor.execute("SELECT level, xp FROM Level WHERE server_id = ? AND user_id = ?", (str(interaction.guild.id), str(target.id)))
    row = local_cursor.fetchone()
    lvl, xp = row if row else (0, 0)
    next_xp = xp_required_for_level(lvl + 1)
    avatar_bytes = await target.display_avatar.read()
    img = await asyncio.to_thread(generate_level_image, avatar_bytes, lvl, xp, next_xp)
    await interaction.followup.send(file=discord.File(fp=img, filename="level.png"))


@client.tree.command(name="leaderboard", description="View a top 10 leaderboard")
@app_commands.choices(type=[
    app_commands.Choice(name="Balance", value="balance"),
    app_commands.Choice(name="Card", value="card"),
    app_commands.Choice(name="Level", value="level"),
])
async def leaderboard(interaction: discord.Interaction, type: app_commands.Choice[str]):
    await interaction.response.defer()
    rows_data, title, header_label = await build_leaderboard_rows(type.value, interaction.guild.id)

    if not rows_data:
        return await interaction.followup.send(embed=discord.Embed(description="No data yet for this leaderboard.", color=discord.Color.orange()))

    img = await asyncio.to_thread(generate_leaderboard_image, title, header_label, rows_data)
    view = LeaderboardView(interaction.guild.id)
    await interaction.followup.send(file=discord.File(fp=img, filename="leaderboard.png"), view=view)



@client.tree.command(name="help", description="List all available commands and how to play")
async def help(interaction: discord.Interaction):
    pages = [
    # Page 1: Welcome Page
    "# **Welcome to Anime TCG**\n\nYou can collect your Anime TCG in the #**Anime TCG** channel. You can earn coins by chatting with others and by using member commands. You can use either `/` slash commands or `Atcg` prefix commands — both do the same thing. If you find any problem or bug in the Anime TCG you can report it to the owner. Play responsibly and start collecting.",

    # Page 2: Economy & Basics
    "**💰 Economy & Basics**\n\n**1. `/balance [user]`**\nCheck your (or another user's) coin balance.\n\n**2. `/beg`**\nAsk for coins (30m cooldown).\n\n**3. `/daily`**\nClaim daily coins (resets at midnight).\n\n**4. `/account`**\nSet your profile to Public or Private.\n\n**5. `/rank`**\nCheck your collection points and rank.\n\n**6. `/burn`**\nGet 50% of card value by burning card.\n\n**7. `/bulk_burn`**\nBurn multiple cards at once by filter.\n\n**8. `/cointoss`**\nBet your coins on a coin flip.",

    # Page 3: Gacha & Collecting
    "**🎴 Gacha & Collecting**\n\n**9. `/gacha`**\nSpend coins to pull a random card.\n\n**10. `/bulk_gacha`**\nPull multiple cards at once.\n\n**11. `/inventory [user]`**\nView a collection with sorting and filters.\n\n**12. `/card_list`**\nBrowse all cards with sorting and filters.\n\n**13. `/view_card`**\nInspect a specific card's details and image.\n\n**14. `/rarity_list`**\nView all card rarities and drop chances.\n\n**15. `/crate`**\nOpen one of your crates.\n\n**16. `/level`**\nCheck your level and XP progress.",

    # Page 4: Social & Trading
    "**🤝 Social & Trading**\n\n**17. `/gift_card`**\nGive a card to another player.\n\n**18. `/gift_coin`**\nGive coins to another player.\n\n**19. `/trade`**\nTrade cards with another player.",

    # Page 5: Market & Leaderboards
    "**⚖️ Market & Leaderboards**\n\n**20. `/market`**\nBrowse cards for sale.\n\n**21. `/market_sell`**\nPut a card up for sale.\n\n**22. `/remove_market`**\nCancel your market listing.\n\n**23. `/leaderboard`**\nView the Balance, Card, or Level leaderboard.",

    # Page 6: Prefix Commands — Economy & Basics
    "**💰 Prefix — Economy & Basics**\nUse `Atcg` instead of `/`.\n\n`Atcg balance [@user]`\n`Atcg beg`\n`Atcg daily`\n`Atcg account <public/private>`\n`Atcg rank`\n`Atcg burn <card> [qty]`\n`Atcg bulk burn`\n`Atcg cointoss <amount> <head/tail>` (alias: `ct`)",

    # Page 7: Prefix Commands — Gacha & Collecting
    "**🎴 Prefix — Gacha & Collecting**\n\n`Atcg gacha [no_of_pulls]` — leave blank for a single pull, add a number (max 20) to bulk pull\n`Atcg inventory [rarity] [anime] [@user]`\n`Atcg card list [rarity] [anime]` (aliases: `cardlist`, `cl`)\n`Atcg view card <name/id>` (aliases: `viewcard`, `vc`, `card view`)\n`Atcg rarity list`\n`Atcg crate`\n`Atcg level [@user]`",

    # Page 8: Prefix Commands — Social & Trading
    "**🤝 Prefix — Social & Trading**\n\n`Atcg gift card <@user> <card> [qty]` (aliases: `gcard`, `card gift`)\n`Atcg gift coin <@user> <amount>` (aliases: `gcoin`, `coin gift`)\n`Atcg trade <@user> <card> <amount> <qty>`",

    # Page 9: Prefix Commands — Market & Leaderboards
    "**⚖️ Prefix — Market & Leaderboards**\n\n`Atcg market`\n`Atcg market sell <card> <price> [qty]` (aliases: `ms`, `sell`)\n`Atcg remove market <id>` (aliases: `rm`, `remove`, `market remove`)\n`Atcg leaderboard [balance/card/level]` (alias: `lb`)\n`Atcg help`"
]

    
    view = HelpPaginator(pages)
    # ephemeral=True ensures only the sender can see this yellow embed
    await interaction.response.send_message(embed=view.create_embed(), view=view, ephemeral=True)
            
@client.tree.command(name="bulk_gacha", description="Pull multiple cards at once")
@app_commands.describe(no_of_pulls="Number of cards to pull (1-20)")
async def bulk_gacha(interaction: discord.Interaction, no_of_pulls: int):
    # 1. Immediate validation
    if no_of_pulls > 20:
        embed = discord.Embed(description="❌ You can't pull more than 20 cards at once.", color=discord.Color.red())
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    if no_of_pulls <= 0:
        await interaction.response.send_message("Please enter a number greater than 0.", ephemeral=True)
        return

    # 2. Tell Discord to wait (This stops the "Application not responding" error)
    await interaction.response.defer()

    user_id = str(interaction.user.id)
    gacha_cost = 1000 * no_of_pulls

    # 3. Check Balance
    cursor.execute("SELECT balance FROM users WHERE id = ?", (user_id,))
    user_data = cursor.fetchone()
    balance = user_data[0] if user_data else 0

    if balance < gacha_cost:
        embed = discord.Embed(title="Insufficient Balance", color=discord.Color.red())
        embed.description = f"Your balance is not enough.\n**Balance:** {balance} 🪙\n**Required:** {gacha_cost} 🪙"
        # Since we deferred, we must use followup.send
        await interaction.followup.send(embed=embed)
        return

    # 4. Pulling Logic
    cursor.execute('SELECT name, chance, color FROM rarities')
    rarity_data = cursor.fetchall()
    rarity_names = [r[0] for r in rarity_data]
    rarity_chances = [r[1] for r in rarity_data]
    rarity_colors = {r[0]: r[2] for r in rarity_data}

    pull_results = []

    cursor.execute("SELECT card_id, name, rarity, value, image FROM cards")
    all_cards = cursor.fetchall()
    cards_by_rarity = {}
    for c in all_cards:
        cards_by_rarity.setdefault(c[2], []).append(c)

    pick_counts = {}
    DUPLICATE_DECAY = 0.35

    try:
        for _ in range(no_of_pulls):
            rarity = random.choices(rarity_names, weights=rarity_chances, k=1)[0]
            cards_of_rarity = cards_by_rarity.get(rarity, [])

            if not cards_of_rarity: continue

            weights = [DUPLICATE_DECAY ** pick_counts.get(c[0], 0) for c in cards_of_rarity]
            card = random.choices(cards_of_rarity, weights=weights, k=1)[0]
            c_id, c_name, c_rarity, c_value, c_image = card
            pick_counts[c_id] = pick_counts.get(c_id, 0) + 1

            cursor.execute('SELECT quantity FROM inventory WHERE user_id = ? AND card_id = ?', (user_id, c_id))
            inv_item = cursor.fetchone()

            if inv_item:
                cursor.execute('UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND card_id = ?', (user_id, c_id))
            else:
                cursor.execute('INSERT INTO inventory (user_id, card_id, quantity) VALUES (?, ?, 1)', (user_id, c_id))

            pull_results.append({
                'card_id': c_id, 'name': c_name, 'rarity': rarity,
                'value': c_value, 'image': c_image, 'color': rarity_colors[rarity]
            })
            
            
        pull_results.sort(key=lambda r: RARITY_ORDER.get(r['rarity'], 99))
        # 5. Deduct Balance and Commit
        cursor.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (gacha_cost, user_id))
        conn.commit()

        # 6. Send Response
        view = BulkGachaView(interaction.user, pull_results, len(pull_results))
        await interaction.followup.send(f"🎉 {interaction.user.mention} pulled cards!", embed=view.create_embed(), view=view)
        all_rarities = [r['rarity'] for r in pull_results]
        await award_xp(interaction.guild.id, interaction.user, all_rarities, interaction.channel)

    except Exception as e:
        conn.rollback()
        # Since we deferred, we must use followup.send
        await interaction.followup.send(f"An error occurred during bulk gacha: {e}")



#Prefix commands
# ============================================================
# PREFIX COMMAND SYSTEM ("Atcg ...")
# ============================================================
import difflib

PREFIX = "atcg"

def resolve_card_fuzzy(query):
    """Exact match by name or ID first; falls back to the closest-matching name."""
    local_cursor = conn.cursor()
    local_cursor.execute("SELECT card_id, name, rarity, value, image, anime FROM cards WHERE name = ? OR card_id = ?", (query, query))
    card = local_cursor.fetchone()
    if card:
        return card
    local_cursor.execute("SELECT card_id, name, rarity, value, image, anime FROM cards")
    all_cards = local_cursor.fetchall()
    names = [c[1] for c in all_cards]
    matches = difflib.get_close_matches(query, names, n=1, cutoff=0.4)
    if matches:
        for c in all_cards:
            if c[1] == matches[0]:
                return c
    return None

def extract_rarity_anime(text):
    """Scans free text for a known rarity and/or anime name, case-insensitive, longest name first."""
    local_cursor = conn.cursor()
    local_cursor.execute("SELECT name FROM rarities")
    rarities = [r[0] for r in local_cursor.fetchall()]
    local_cursor.execute("SELECT name FROM anime")
    animes = [r[0] for r in local_cursor.fetchall()]

    lower_text = text.lower()
    found_rarity = next((r for r in sorted(rarities, key=len, reverse=True) if r.lower() in lower_text), None)
    found_anime = next((a for a in sorted(animes, key=len, reverse=True) if a.lower() in lower_text), None)
    return found_rarity, found_anime

def strip_mentions(args):
    return [a for a in args if not (a.startswith("<@") and a.endswith(">"))]


class PrefixSortSelect(ui.Select):
    """Reusable sort dropdown attached to a CardPaginator view for prefix commands."""
    def __init__(self, owner_id, base_query, base_params, sort_templates):
        options = [
            discord.SelectOption(label="Low to High Value", value="value_asc", default=True),
            discord.SelectOption(label="High to Low Value", value="value_desc"),
            discord.SelectOption(label="Low to High ID", value="id_asc"),
            discord.SelectOption(label="High to Low ID", value="id_desc"),
        ]
        super().__init__(placeholder="Sort", options=options)
        self.owner_id = owner_id
        self.base_query = base_query
        self.base_params = base_params
        self.sort_templates = sort_templates

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("❌ This isn't your menu.", ephemeral=True)

        local_cursor = conn.cursor()
        local_cursor.execute(self.base_query + self.sort_templates[self.values[0]], self.base_params)
        results = local_cursor.fetchall()

        for opt in self.options:
            opt.default = (opt.value == self.values[0])

        view = self.view
        view.cards = results
        view.current_page = 0
        await interaction.response.edit_message(embed=view.create_embed(), view=view)


# --- 1. rank ---
async def px_rank(message, args):
    data = get_all_leaderboard_data()
    user_rank = next((i for i, item in enumerate(data) if item["id"] == str(message.author.id)), None)
    if user_rank is None:
        return await message.channel.send("You don't have any cards yet!")
    user_data = data[user_rank]
    s = user_data['stats']
    embed = discord.Embed(title=f"**{message.author.name}**", color=0xFFFF00)
    embed.add_field(name="Stats", value=(
        f"Common: {s['Common']}\nUncommon: {s['Uncommon']}\nRare: {s['Rare']}\n"
        f"Epic: {s['Epic']}\nLegendary: {s['Legendary']}\nSuper Legendary: {s['Super Legendary']}\n"
        f"**Collection Points: {user_data['points']}**\n**Rank: #{user_rank + 1}**"
    ), inline=False)
    await message.channel.send(embed=embed)

# --- 2. inventory ---
async def px_inventory(message, args):
    mentions = message.mentions
    target = mentions[0] if mentions else message.author
    text = " ".join(strip_mentions(args))
    rarity, anime = extract_rarity_anime(text)

    local_cursor = conn.cursor()
    if target.id != message.author.id:
        local_cursor.execute("SELECT account_status FROM users WHERE id = ?", (str(target.id),))
        row = local_cursor.fetchone()
        status = row[0] if row else "public"
        if status == "private":
            embed = discord.Embed(description=f"❌ {target.mention}'s profile is private. You can't check the inventory.", color=discord.Color.red())
            return await message.channel.send(embed=embed)

    base_query = '''SELECT c.card_id, c.name, c.rarity, c.value, c.image, i.quantity FROM inventory i
                     JOIN cards c ON i.card_id = c.card_id WHERE i.user_id = ?'''
    params = [str(target.id)]
    if rarity:
        base_query += " AND c.rarity = ?"; params.append(rarity)
    if anime:
        base_query += " AND c.anime = ?"; params.append(anime)

    sort_templates = {
        "value_asc": " ORDER BY c.value ASC",
        "value_desc": " ORDER BY c.value DESC",
        "id_asc": " ORDER BY CAST(c.card_id AS INTEGER) ASC",
        "id_desc": " ORDER BY CAST(c.card_id AS INTEGER) DESC",
    }
    local_cursor.execute(base_query + sort_templates["value_asc"], params)
    items = local_cursor.fetchall()

    if not items:
        return await message.channel.send(embed=discord.Embed(description="⚠️ No cards match those filters.", color=discord.Color.orange()))

    title = "Your Collection" if target.id == message.author.id else f"{target.name}'s Collection"
    view = CardPaginator(items, 0, title)
    view.add_item(PrefixSortSelect(message.author.id, base_query, params, sort_templates))
    await message.channel.send(embed=view.create_embed(), view=view)

# --- 3. view_card ---
async def px_view_card(message, args):
    if not args:
        return await message.channel.send("❌ Usage: `Atcg view card <name/id>`")
    query = " ".join(args)
    card = resolve_card_fuzzy(query)
    if not card:
        return await message.channel.send("❌ Card not found.")
    view = CardPaginator([card[:5]], 0, "Card Details")
    await message.channel.send(embed=view.create_embed())

# --- 4. rarity_list ---
async def px_rarity_list(message, args):
    cursor.execute('SELECT name, chance FROM rarities ORDER BY chance DESC')
    rows = cursor.fetchall()
    desc = "\n".join([f"✨ **{r[0]}**: {r[1]}%" for r in rows])
    embed = discord.Embed(title="Rarity Tiers & Drop Chances", description=desc, color=0xFFD700)
    await message.channel.send(embed=embed)

# --- 5 & 6. gacha / bulk_gacha merged: "Atcg gacha [no_of_pulls]" ---
async def px_gacha(message, args):
    if args and args[0].isdigit():
        await px_bulk_gacha_inner(message, int(args[0]))
    else:
        await px_gacha_inner(message)

async def px_gacha_inner(message):
    cursor.execute("SELECT value FROM config WHERE key = 'gacha_cost'")
    res = cursor.fetchone()
    cost = int(res[0]) if res else 1000

    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(message.author.id),))
    row = cursor.fetchone()
    balance = row[0] if row else 0

    if balance < cost:
        embed = discord.Embed(description=f"❌ You need **{cost}** 🪙 to pull!\n**Your balance:** {balance} 🪙", color=discord.Color.red())
        return await message.channel.send(embed=embed)

    cursor.execute('SELECT name, chance FROM rarities')
    rarity_data = cursor.fetchall()
    rarities = [r[0] for r in rarity_data]
    weights = [r[1] for r in rarity_data]
    chosen_rarity = random.choices(rarities, weights=weights, k=1)[0]

    cursor.execute('SELECT * FROM cards WHERE rarity = ? ORDER BY RANDOM() LIMIT 1', (chosen_rarity,))
    card = cursor.fetchone()
    if not card:
        return await message.channel.send(f"⚠️ No cards found for rarity: **{chosen_rarity}**.")

    cursor.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (cost, str(message.author.id)))
    cursor.execute('''INSERT INTO inventory (user_id, card_id, quantity) VALUES (?, ?, 1)
                      ON CONFLICT(user_id, card_id) DO UPDATE SET quantity = quantity + 1''', (str(message.author.id), card[0]))
    conn.commit()

    cursor.execute('SELECT color FROM rarities WHERE name = ?', (card[2],))
    color_res = cursor.fetchone()
    embed_color = int(color_res[0].replace("#", ""), 16) if color_res else 0xFFFF00

    embed = discord.Embed(title="✨ GACHA PULL ✨", color=embed_color)
    embed.add_field(name=f"**{card[1]}**", value=f"**Rarity:** {card[2]}\n**Value:** {card[3]} 🪙\n**Card ID:** `{card[0]}`", inline=False)
    embed.set_image(url=card[4])
    embed.set_footer(text=f"Remaining Balance: {balance - cost} 🪙")

    await message.channel.send(content=f"🎉 {message.author.mention} pulled a card!", embed=embed)
    await award_xp(message.guild.id, message.author, [chosen_rarity], message.channel)

async def px_bulk_gacha_inner(message, no_of_pulls):
    if no_of_pulls > 20:
        return await message.channel.send(embed=discord.Embed(description="❌ You can't pull more than 20 cards at once.", color=discord.Color.red()))
    if no_of_pulls <= 0:
        return await message.channel.send("Please enter a number greater than 0.")

    user_id = str(message.author.id)
    gacha_cost = 1000 * no_of_pulls

    cursor.execute("SELECT balance FROM users WHERE id = ?", (user_id,))
    user_data = cursor.fetchone()
    balance = user_data[0] if user_data else 0

    if balance < gacha_cost:
        embed = discord.Embed(title="Insufficient Balance", color=discord.Color.red())
        embed.description = f"Your balance is not enough.\n**Balance:** {balance} 🪙\n**Required:** {gacha_cost} 🪙"
        return await message.channel.send(embed=embed)

    cursor.execute('SELECT name, chance, color FROM rarities')
    rarity_data = cursor.fetchall()
    rarity_names = [r[0] for r in rarity_data]
    rarity_chances = [r[1] for r in rarity_data]
    rarity_colors = {r[0]: r[2] for r in rarity_data}

    pull_results = []
    cursor.execute("SELECT card_id, name, rarity, value, image FROM cards")
    all_cards = cursor.fetchall()
    cards_by_rarity = {}
    for c in all_cards:
        cards_by_rarity.setdefault(c[2], []).append(c)

    pick_counts = {}
    DUPLICATE_DECAY = 0.35

    try:
        for _ in range(no_of_pulls):
            rarity = random.choices(rarity_names, weights=rarity_chances, k=1)[0]
            cards_of_rarity = cards_by_rarity.get(rarity, [])
            if not cards_of_rarity:
                continue
            weights = [DUPLICATE_DECAY ** pick_counts.get(c[0], 0) for c in cards_of_rarity]
            card = random.choices(cards_of_rarity, weights=weights, k=1)[0]
            c_id, c_name, c_rarity, c_value, c_image = card
            pick_counts[c_id] = pick_counts.get(c_id, 0) + 1

            cursor.execute('SELECT quantity FROM inventory WHERE user_id = ? AND card_id = ?', (user_id, c_id))
            inv_item = cursor.fetchone()
            if inv_item:
                cursor.execute('UPDATE inventory SET quantity = quantity + 1 WHERE user_id = ? AND card_id = ?', (user_id, c_id))
            else:
                cursor.execute('INSERT INTO inventory (user_id, card_id, quantity) VALUES (?, ?, 1)', (user_id, c_id))

            pull_results.append({'card_id': c_id, 'name': c_name, 'rarity': rarity, 'value': c_value, 'image': c_image, 'color': rarity_colors[rarity]})

        pull_results.sort(key=lambda r: RARITY_ORDER.get(r['rarity'], 99))
        cursor.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (gacha_cost, user_id))
        conn.commit()

        view = BulkGachaView(message.author, pull_results, len(pull_results))
        await message.channel.send(f"🎉 {message.author.mention} pulled cards!", embed=view.create_embed(), view=view)
        all_rarities = [r['rarity'] for r in pull_results]
        await award_xp(message.guild.id, message.author, all_rarities, message.channel)
    except Exception as e:
        conn.rollback()
        await message.channel.send(f"An error occurred during bulk gacha: {e}")

# --- 7. trade ---
async def px_trade(message, args):
    if not message.mentions:
        return await message.channel.send("❌ Usage: `Atcg trade <@user> <card name/id> <amount> <quantity>`")
    target_user = message.mentions[0]
    rest = strip_mentions(args)
    if len(rest) < 3 or not rest[-1].isdigit() or not rest[-2].isdigit():
        return await message.channel.send("❌ Usage: `Atcg trade <@user> <card name/id> <amount> <quantity>`")
    quantity = int(rest[-1])
    trade_amount = int(rest[-2])
    card_name_or_id = " ".join(rest[:-2])

    cursor.execute('SELECT c.card_id, c.name, c.rarity, c.value, c.image, i.quantity FROM inventory i JOIN cards c ON i.card_id = c.card_id WHERE i.user_id = ? AND (c.name = ? OR c.card_id = ?)', (str(message.author.id), card_name_or_id, card_name_or_id))
    card = cursor.fetchone()
    if not card or card[5] < quantity:
        return await message.channel.send("❌ You don't have enough copies!")
    embed = discord.Embed(title="🤝 Trade Offer", color=discord.Color.blue())
    embed.add_field(name="Details", value=f"**Seller:** {message.author.name}\n**Card:** {card[1]}\n**Qty:** {quantity}\n**Total:** {trade_amount * quantity} 🪙")
    embed.set_image(url=card[4])
    try:
        await target_user.send(embed=embed, view=SaleView(message.author, target_user, card, trade_amount, quantity))
        await message.channel.send(f"✅ Offer sent to {target_user.name}!")
    except:
        await message.channel.send("❌ User has DMs closed!")

# --- 8. card_list ---
async def px_card_list(message, args):
    text = " ".join(args)
    rarity, anime = extract_rarity_anime(text)

    base_query = "SELECT card_id, name, rarity, value, image, anime FROM cards WHERE 1=1"
    params = []
    if rarity:
        base_query += " AND rarity = ?"; params.append(rarity)
    if anime:
        base_query += " AND anime = ?"; params.append(anime)

    sort_templates = {
        "value_asc": " ORDER BY value ASC",
        "value_desc": " ORDER BY value DESC",
        "id_asc": " ORDER BY CAST(card_id AS INTEGER) ASC",
        "id_desc": " ORDER BY CAST(card_id AS INTEGER) DESC",
    }
    local_cursor = conn.cursor()
    local_cursor.execute(base_query + sort_templates["value_asc"], params)
    cards = local_cursor.fetchall()

    if not cards:
        return await message.channel.send(embed=discord.Embed(description="⚠️ No cards match those filters.", color=discord.Color.orange()))

    view = CardPaginator(cards, 0, "Card List")
    view.add_item(PrefixSortSelect(message.author.id, base_query, params, sort_templates))
    await message.channel.send(embed=view.create_embed(), view=view)

# --- 9. burn ---
async def px_burn(message, args):
    if not args:
        return await message.channel.send("❌ Usage: `Atcg burn <card name/id> [quantity]`")
    if args[-1].isdigit():
        quantity = int(args[-1])
        card_name = " ".join(args[:-1])
    else:
        quantity = 1
        card_name = " ".join(args)

    if quantity <= 0 or not card_name:
        return await message.channel.send("❌ Usage: `Atcg burn <card name/id> [quantity]`")

    user_id = str(message.author.id)
    cursor.execute("SELECT card_id, name, rarity, value, image FROM cards WHERE name = ? OR card_id = ?", (card_name, card_name))
    card = cursor.fetchone()
    if not card:
        return await message.channel.send(embed=discord.Embed(description="❌ You don't have that card", color=discord.Color.red()))

    c_id, name, rarity, value, image = card
    burn_value_per_card = int(value * 0.5)
    total_received = burn_value_per_card * quantity

    cursor.execute("SELECT quantity FROM inventory WHERE user_id = ? AND card_id = ?", (user_id, c_id))
    inv_data = cursor.fetchone()
    if not inv_data or inv_data[0] < quantity:
        return await message.channel.send(embed=discord.Embed(description="❌ You don't have enough cards", color=discord.Color.red()))

    if inv_data[0] == quantity:
        cursor.execute("DELETE FROM inventory WHERE user_id = ? AND card_id = ?", (user_id, c_id))
    else:
        cursor.execute("UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND card_id = ?", (quantity, user_id, c_id))
    cursor.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (total_received, user_id))
    conn.commit()

    embed = discord.Embed(description=f"**{message.author.name}** successfully burned cards", color=discord.Color.green())
    embed.add_field(name="Name", value=name, inline=True)
    embed.add_field(name="Rarity", value=rarity, inline=True)
    embed.add_field(name="Id", value=f"`{c_id}`", inline=True)
    embed.add_field(name="Value", value=f"{value} 🪙", inline=True)
    embed.add_field(name="Quantity", value=str(quantity), inline=True)
    embed.add_field(name="Amount received", value=f"{total_received} 🪙", inline=True)
    if image:
        embed.set_image(url=image)
    await message.channel.send(embed=embed)

# --- 10. bulk_burn ---
class PrefixBulkBurnView(ui.View):
    def __init__(self, owner_id):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.filter_value = None
        self.rarity_value = None
        self.anime_value = None

    async def interaction_check(self, interaction: discord.Interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ This isn't your menu.", ephemeral=True)
            return False
        return True

    @ui.select(placeholder="Select Filter (required)", options=[
        discord.SelectOption(label="Burn all cards", value="all"),
        discord.SelectOption(label="Burn all duplicate cards", value="duplicates"),
    ])
    async def select_filter(self, interaction: discord.Interaction, select: ui.Select):
        self.filter_value = select.values[0]
        await interaction.response.defer()
        await self.maybe_run(interaction)

    async def maybe_run(self, interaction):
        if self.filter_value:
            await run_bulk_burn(interaction.message, self.owner_id, self.filter_value, self.rarity_value, self.anime_value)
            self.stop()

class PrefixBulkBurnRaritySelect(ui.Select):
    def __init__(self, parent_view):
        cursor.execute("SELECT name FROM rarities")
        options = [discord.SelectOption(label=r[0], value=r[0]) for r in cursor.fetchall()][:25]
        super().__init__(placeholder="Select Rarity (optional)", options=options)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.rarity_value = self.values[0]
        await interaction.response.defer()

class PrefixBulkBurnAnimeSelect(ui.Select):
    def __init__(self, parent_view):
        cursor.execute("SELECT name FROM anime")
        options = [discord.SelectOption(label=r[0], value=r[0]) for r in cursor.fetchall()][:25]
        super().__init__(placeholder="Select Anime (optional)", options=options)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.anime_value = self.values[0]
        await interaction.response.defer()

async def run_bulk_burn(message_or_channel_msg, user_id_int, filter_value, rarity, anime):
    local_cursor = conn.cursor()
    query = '''SELECT c.card_id, c.rarity, c.value, i.quantity FROM inventory i
               JOIN cards c ON i.card_id = c.card_id WHERE i.user_id = ?'''
    params = [str(user_id_int)]
    if rarity:
        query += " AND c.rarity = ?"; params.append(rarity)
    if anime:
        query += " AND c.anime = ?"; params.append(anime)
    local_cursor.execute(query, params)
    owned = local_cursor.fetchall()

    rarity_counts = {"Common": 0, "Uncommon": 0, "Rare": 0, "Epic": 0, "Legendary": 0, "Super Legendary": 0}
    total_coins = 0
    total_burned = 0
    for card_id, card_rarity, value, quantity in owned:
        burn_qty = quantity if filter_value == "all" else quantity - 1
        if burn_qty <= 0:
            continue
        coins = int(value * burn_qty * 0.5)
        total_coins += coins
        total_burned += burn_qty
        rarity_counts[card_rarity] = rarity_counts.get(card_rarity, 0) + burn_qty
        if burn_qty == quantity:
            local_cursor.execute("DELETE FROM inventory WHERE user_id = ? AND card_id = ?", (str(user_id_int), card_id))
        else:
            local_cursor.execute("UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND card_id = ?", (burn_qty, str(user_id_int), card_id))

    channel = message_or_channel_msg.channel
    if total_burned == 0:
        return await channel.send(embed=discord.Embed(description="⚠️ No cards matched those filters to burn.", color=discord.Color.orange()))

    local_cursor.execute("INSERT INTO users (id, balance) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET balance = balance + ?", (str(user_id_int), total_coins, total_coins))
    conn.commit()

    embed = discord.Embed(
        title=f"{total_burned} Cards burned.",
        description=(
            f"Common: {rarity_counts['Common']} Cards\nUncommon: {rarity_counts['Uncommon']} Cards\nRare: {rarity_counts['Rare']} Cards\n"
            f"Epic: {rarity_counts['Epic']} Cards\nLegendary: {rarity_counts['Legendary']} Cards\nSuper Legendary: {rarity_counts['Super Legendary']} Cards\n\n"
            f"<@{user_id_int}> received **{total_coins}** 🪙."
        ),
        color=discord.Color.green()
    )
    await channel.send(embed=embed)

async def px_bulk_burn(message, args):
    view = PrefixBulkBurnView(message.author.id)
    view.add_item(PrefixBulkBurnRaritySelect(view))
    view.add_item(PrefixBulkBurnAnimeSelect(view))
    await message.channel.send("Select a filter (required), and optionally a rarity and anime:", view=view)

# --- 11. market_sell ---
async def px_market_sell(message, args):
    if len(args) >= 2 and args[-1].isdigit() and args[-2].isdigit():
        quantity = int(args[-1]); price = int(args[-2]); name_tokens = args[:-2]
    elif len(args) >= 1 and args[-1].isdigit():
        price = int(args[-1]); quantity = 1; name_tokens = args[:-1]
    else:
        return await message.channel.send("❌ Usage: `Atcg market sell <card name/id> <price> [quantity]`")

    card_name = " ".join(name_tokens)
    if price < 0 or quantity <= 0 or not card_name:
        return await message.channel.send("❌ Invalid price or quantity.")

    cursor.execute('''SELECT i.quantity, c.card_id, c.name FROM inventory i
                      JOIN cards c ON i.card_id = c.card_id
                      WHERE i.user_id = ? AND (c.name = ? OR c.card_id = ?)''',
                   (str(message.author.id), card_name, card_name))
    row = cursor.fetchone()
    if not row or row[0] < quantity:
        return await message.channel.send("❌ You don't have enough of that card to sell!")

    card_id, real_name = row[1], row[2]
    cursor.execute('UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND card_id = ?', (quantity, str(message.author.id), card_id))
    cursor.execute('DELETE FROM inventory WHERE quantity <= 0')
    cursor.execute('INSERT INTO market (seller_id, card_id, price, quantity) VALUES (?, ?, ?, ?)', (str(message.author.id), card_id, price, quantity))
    conn.commit()
    await message.channel.send(f"✅ Listed {quantity}x **{real_name}** for {price} 🪙 each.")

# --- 12. market ---
async def px_market(message, args):
    cursor.execute('''SELECT m.selling_id, m.seller_id, m.price, m.quantity,
                             c.card_id, c.name, c.rarity, c.value, c.image
                      FROM market m JOIN cards c ON m.card_id = c.card_id''')
    listings = cursor.fetchall()
    if not listings:
        return await message.channel.send(embed=discord.Embed(description="🛒 The market is currently empty!", color=discord.Color.orange()))
    view = MarketPaginator(listings, client)
    await message.channel.send(embed=await view.create_embed(), view=view)

# --- 13. remove_market ---
async def px_remove_market(message, args):
    if not args or not args[0].isdigit():
        return await message.channel.send("❌ Usage: `Atcg remove market <selling id>`")
    listing_id = int(args[0])
    cursor.execute('''SELECT m.seller_id, m.card_id, m.quantity, c.name FROM market m
                      JOIN cards c ON m.card_id = c.card_id WHERE m.selling_id = ?''', (listing_id,))
    listing = cursor.fetchone()
    if not listing:
        return await message.channel.send(embed=discord.Embed(description="⚠️ Market listing not found.", color=discord.Color.red()))
    seller_id, card_id, qty, card_name = listing
    if str(message.author.id) != str(seller_id):
        return await message.channel.send(embed=discord.Embed(description=f"{message.author.mention}, You can't remove someone else's card.", color=discord.Color.red()))
    cursor.execute('INSERT INTO inventory (user_id, card_id, quantity) VALUES (?, ?, ?) ON CONFLICT(user_id, card_id) DO UPDATE SET quantity = quantity + ?', (str(message.author.id), card_id, qty, qty))
    cursor.execute('DELETE FROM market WHERE selling_id = ?', (listing_id,))
    conn.commit()
    await message.channel.send(embed=discord.Embed(description=f"{message.author.mention}, Successfully removed **{card_name}** from the market.", color=discord.Color.green()))

# --- 14. gift_card ---
async def px_gift_card(message, args):
    if not message.mentions:
        return await message.channel.send("❌ Usage: `Atcg gift card <@user> <card name/id> [quantity]`")
    target_user = message.mentions[0]
    rest = strip_mentions(args)
    if rest and rest[-1].isdigit():
        quantity = int(rest[-1]); card_name = " ".join(rest[:-1])
    else:
        quantity = 1; card_name = " ".join(rest)

    if target_user.id == message.author.id:
        return await message.channel.send("❌ You can't gift cards to yourself!")
    if quantity <= 0 or not card_name:
        return await message.channel.send("❌ Usage: `Atcg gift card <@user> <card name/id> [quantity]`")

    cursor.execute('''SELECT c.card_id, i.quantity, c.name, c.rarity, c.value, c.image
                      FROM inventory i JOIN cards c ON i.card_id = c.card_id
                      WHERE i.user_id = ? AND (c.name = ? OR c.card_id = ?)''',
                   (str(message.author.id), card_name, card_name))
    card = cursor.fetchone()
    if not card:
        return await message.channel.send(embed=discord.Embed(description="⚠️ You don't have that card in inventory", color=discord.Color.red()))
    if card[1] < quantity:
        return await message.channel.send(embed=discord.Embed(description="⚠️ You don't have that much card in inventory", color=discord.Color.red()))

    card_id, _, name, rarity, value, image = card
    cursor.execute('UPDATE inventory SET quantity = quantity - ? WHERE user_id = ? AND card_id = ?', (quantity, str(message.author.id), card_id))
    cursor.execute('INSERT INTO inventory (user_id, card_id, quantity) VALUES (?, ?, ?) ON CONFLICT(user_id, card_id) DO UPDATE SET quantity = quantity + ?', (str(target_user.id), card_id, quantity, quantity))
    cursor.execute('DELETE FROM inventory WHERE quantity <= 0')
    conn.commit()

    cursor.execute('SELECT COUNT(DISTINCT user_id) FROM inventory WHERE card_id = ?', (card_id,))
    owners = cursor.fetchone()[0]

    dm_embed = discord.Embed(description=f"{message.author.mention} has gifted you **{name}** 🎁", color=discord.Color.green())
    dm_embed.add_field(name="Details", value=(
        f"**Name of card:** {name}\n**Rarity:** {rarity}\n**Value:** {value} 🪙\n**Card id:** `{card_id}`\n**Quantity:** {quantity}\n**Owners:** {owners} 👥"
    ), inline=False)
    dm_embed.set_image(url=image)

    try:
        await target_user.send(embed=dm_embed)
        await message.channel.send(f"✅ Successfully gifted {quantity}x {name} to {target_user.name}!")
    except discord.Forbidden:
        await message.channel.send(f"✅ Successfully gifted to {target_user.name}, but their DMs are closed so I couldn't notify them.")

# --- 15. gift_coin ---
async def px_gift_coin(message, args):
    if not message.mentions:
        return await message.channel.send("❌ Usage: `Atcg gift coin <@user> <amount>`")
    target_user = message.mentions[0]
    rest = strip_mentions(args)
    if not rest or not rest[-1].isdigit():
        return await message.channel.send("❌ Usage: `Atcg gift coin <@user> <amount>`")
    amount = int(rest[-1])

    if target_user.id == message.author.id:
        return await message.channel.send("❌ You can't gift coins to yourself!")
    if amount <= 0:
        return await message.channel.send("❌ You must gift at least 1 coin!")

    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(message.author.id),))
    row = cursor.fetchone()
    balance = row[0] if row else 0
    if balance < amount:
        return await message.channel.send(embed=discord.Embed(description=f"⚠️ You don't have enough balance\n**Balance:** {balance} 🪙", color=discord.Color.red()))

    cursor.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (amount, str(message.author.id)))
    cursor.execute('INSERT INTO users (id, balance) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET balance = balance + ?', (str(target_user.id), amount, amount))
    conn.commit()

    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(target_user.id),))
    receiver_balance = cursor.fetchone()[0]
    dm_embed = discord.Embed(description=f"{message.author.mention} has gifted you **{amount}** 🪙 coins 🎁\n**Balance:** {receiver_balance} 🪙", color=discord.Color.green())
    try:
        await target_user.send(embed=dm_embed)
        await message.channel.send(f"✅ Successfully gifted {amount} coins to {target_user.name}!")
    except discord.Forbidden:
        await message.channel.send(f"✅ Successfully gifted to {target_user.name}, but their DMs are closed so I couldn't notify them.")

# --- 16. crate ---
async def px_crate(message, args):
    local_cursor = conn.cursor()
    local_cursor.execute('''
        SELECT c.crate_id, c.crate_name, ci.quantity FROM crate_inventory ci
        JOIN Crate c ON ci.crate_id = c.crate_id
        WHERE ci.user_id = ? AND ci.quantity > 0 ORDER BY c.crate_name
    ''', (str(message.author.id),))
    crates = local_cursor.fetchall()
    if not crates:
        return await message.channel.send(embed=discord.Embed(description="❌ You don't have any crates to open.", color=discord.Color.red()))
    embed = discord.Embed(title="Open a Crate <:crate:1537797263714295819>", description="Select a crate from below to open it.", color=discord.Color.red())
    await message.channel.send(embed=embed, view=CrateOpenView(message.author.id, crates))

# --- 17. account ---
async def px_account(message, args):
    if not args or args[0].lower() not in ("public", "private"):
        return await message.channel.send("❌ Usage: `Atcg account <public/private>`")
    status_value = args[0].lower()
    cursor.execute('INSERT INTO users (id, account_status) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET account_status = ?', (str(message.author.id), status_value, status_value))
    conn.commit()
    await message.channel.send(f"✅ Your account is now **{status_value.capitalize()}**.")

# --- 18. balance ---
async def px_balance(message, args):
    target = message.mentions[0] if message.mentions else message.author
    if target.id != message.author.id:
        cursor.execute('SELECT account_status FROM users WHERE id = ?', (str(target.id),))
        row = cursor.fetchone()
        status = row[0] if row else "public"
        if status == "private":
            return await message.channel.send(embed=discord.Embed(description=f"❌ {target.mention}'s profile is private. You can't check the balance.", color=discord.Color.red()))
    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(target.id),))
    row = cursor.fetchone()
    bal = row[0] if row else 0
    embed = discord.Embed(title=f"{target.name}'s balance", description=f"**Balance:** {bal} 🪙", color=0xFFFF00)
    await message.channel.send(embed=embed)

# --- 19. beg ---
async def px_beg(message, args):
    now = datetime.datetime.now()
    cursor.execute('SELECT last_beg, balance FROM users WHERE id = ?', (str(message.author.id),))
    row = cursor.fetchone()
    if row and row[0]:
        last_time = datetime.datetime.fromisoformat(row[0])
        if now < last_time + datetime.timedelta(minutes=30):
            diff = (last_time + datetime.timedelta(minutes=30)) - now
            minutes = int(diff.total_seconds() // 60)
            return await message.channel.send(embed=discord.Embed(description=f"{message.author.mention}\nYou can't beg now. God is busy fulfilling the wishes of others. Please wait **{minutes}** more minutes.", color=discord.Color.red()))

    amount = random.randint(1, 250)
    cursor.execute('INSERT INTO users (id, balance, last_beg) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET balance = balance + ?, last_beg = ?', (str(message.author.id), amount, now.isoformat(), amount, now.isoformat()))
    conn.commit()
    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(message.author.id),))
    new_bal = cursor.fetchone()[0]
    embed = discord.Embed(title=f"{message.author.name}", description=f"God showed mercy on you. You received **{amount}** 🪙 coins!\n**Balance:** {new_bal} 🪙", color=0xFFFF00)
    await message.channel.send(embed=embed)

# --- 20. daily ---
async def px_daily(message, args):
    now = datetime.datetime.now()
    cursor.execute('SELECT last_daily, balance FROM users WHERE id = ?', (str(message.author.id),))
    row = cursor.fetchone()
    if row and row[0]:
        last_time = datetime.datetime.fromisoformat(row[0])
        if now.date() == last_time.date():
            tomorrow = datetime.datetime.combine(now.date() + datetime.timedelta(days=1), datetime.time.min)
            diff = tomorrow - now
            hours, remainder = divmod(int(diff.total_seconds()), 3600)
            minutes, _ = divmod(remainder, 60)
            return await message.channel.send(embed=discord.Embed(description=f"{message.author.mention}\nYou've already claimed your daily reward. Please wait **{hours}h {minutes}m** to claim again.", color=discord.Color.red()))

    amount = random.randint(500, 1000)
    cursor.execute('INSERT INTO users (id, balance, last_daily) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET balance = balance + ?, last_daily = ?', (str(message.author.id), amount, now.isoformat(), amount, now.isoformat()))
    conn.commit()
    cursor.execute('SELECT balance FROM users WHERE id = ?', (str(message.author.id),))
    new_bal = cursor.fetchone()[0]
    await message.channel.send(embed=discord.Embed(description=f"{message.author.mention} claimed their daily reward!\n**Amount:** {amount} 🪙\n**Balance:** {new_bal} 🪙", color=0xFFFF00))

# --- 21. cointoss ---
async def px_cointoss(message, args):
    amount = None
    call = None
    for a in args:
        if a.isdigit() and amount is None:
            amount = int(a)
        elif a.lower() in ("head", "h", "tail", "t") and call is None:
            call = "Head" if a.lower() in ("head", "h") else "Tail"
    if amount is None or call is None:
        return await message.channel.send("❌ Usage: `Atcg cointoss <amount> <head/tail>`")

    user_id = str(message.author.id)
    cursor.execute('SELECT balance FROM users WHERE id = ?', (user_id,))
    row = cursor.fetchone()
    balance = row[0] if row else 0

    if amount <= 0 or amount > balance:
        return await message.channel.send(embed=discord.Embed(description=f"❌ You have insufficient balance.\n**Your balance:** {balance} 🪙", color=discord.Color.red()))

    outcome = random.choice(["Head", "Tail"])
    if outcome == call:
        winnings = amount * 2
        cursor.execute('UPDATE users SET balance = balance + ? WHERE id = ?', (winnings - amount, user_id))
        conn.commit()
        embed = discord.Embed(description=f"It was **{outcome}** and your call was **{call}**\nYou won **{winnings}** 🪙", color=discord.Color.green())
    else:
        cursor.execute('UPDATE users SET balance = balance - ? WHERE id = ?', (amount, user_id))
        conn.commit()
        embed = discord.Embed(description=f"It was **{outcome}** and your call was **{call}**\nYou lost **{amount}** 🪙", color=discord.Color.red())
    await message.channel.send(embed=embed)

# --- 22. level ---
async def px_level(message, args):
    target = message.mentions[0] if message.mentions else message.author
    local_cursor = conn.cursor()
    local_cursor.execute("SELECT level, xp FROM Level WHERE server_id = ? AND user_id = ?", (str(message.guild.id), str(target.id)))
    row = local_cursor.fetchone()
    lvl, xp = row if row else (0, 0)
    next_xp = xp_required_for_level(lvl + 1)
    avatar_bytes = await target.display_avatar.read()
    img = await asyncio.to_thread(generate_level_image, avatar_bytes, lvl, xp, next_xp)
    await message.channel.send(file=discord.File(fp=img, filename="level.png"))

# --- 23. leaderboard ---
async def px_leaderboard(message, args):
    type_value = "balance"
    if args:
        arg_lower = args[0].lower()
        if arg_lower.startswith("bal"):
            type_value = "balance"
        elif arg_lower.startswith("card"):
            type_value = "card"
        elif arg_lower.startswith("lvl") or arg_lower.startswith("level"):
            type_value = "level"

    rows_data, title, header_label = await build_leaderboard_rows(type_value, message.guild.id)
    if not rows_data:
        return await message.channel.send(embed=discord.Embed(description="No data yet for this leaderboard.", color=discord.Color.orange()))
    img = await asyncio.to_thread(generate_leaderboard_image, title, header_label, rows_data)
    view = LeaderboardView(message.guild.id)
    await message.channel.send(file=discord.File(fp=img, filename="leaderboard.png"), view=view)

# --- 24. help ---
async def px_help(message, args):
    view = HelpPaginator(HELP_PAGES)
    await message.channel.send(embed=view.create_embed(), view=view)


# --- Dispatch table: each entry is (alias word-tuple, handler function) ---
PREFIX_ALIASES = [
    (("rank",), px_rank),
    (("inventory",), px_inventory),
    (("view", "card"), px_view_card),
    (("card", "view"), px_view_card),
    (("viewcard",), px_view_card),
    (("vc",), px_view_card),
    (("rarity", "list"), px_rarity_list),
    (("gacha",), px_gacha),
    (("trade",), px_trade),
    (("card", "list"), px_card_list),
    (("cardlist",), px_card_list),
    (("cl",), px_card_list),
    (("bulk", "burn"), px_bulk_burn),
    (("burn",), px_burn),
    (("market", "sell"), px_market_sell),
    (("ms",), px_market_sell),
    (("sell",), px_market_sell),
    (("remove", "market"), px_remove_market),
    (("market", "remove"), px_remove_market),
    (("rm",), px_remove_market),
    (("remove",), px_remove_market),
    (("market",), px_market),
    (("gift", "card"), px_gift_card),
    (("card", "gift"), px_gift_card),
    (("gcard",), px_gift_card),
    (("gift", "coin"), px_gift_coin),
    (("coin", "gift"), px_gift_coin),
    (("gcoin",), px_gift_coin),
    (("crate",), px_crate),
    (("account",), px_account),
    (("balance",), px_balance),
    (("beg",), px_beg),
    (("daily",), px_daily),
    (("cointoss",), px_cointoss),
    (("ct",), px_cointoss),
    (("level",), px_level),
    (("leaderboard",), px_leaderboard),
    (("lb",), px_leaderboard),
    (("help",), px_help),
]
# Longest alias first, so "market sell" is checked before bare "market"
PREFIX_ALIASES.sort(key=lambda pair: -len(pair[0]))

async def route_prefix_command(message):
    content = message.content.strip()
    if not content.lower().startswith(PREFIX):
        return False
    remainder = content[len(PREFIX):].strip()
    if not remainder:
        return False

    tokens = remainder.split()
    lowered = [t.lower() for t in tokens]

    for alias_tuple, handler in PREFIX_ALIASES:
        n = len(alias_tuple)
        if lowered[:n] == list(alias_tuple):
            args = tokens[n:]
            try:
                await handler(message, args)
            except Exception as e:
                await message.channel.send(f"⚠️ Something went wrong running that command: {e}")
            return True
    return False
    



if __name__ == '__main__':
    Thread(target=run_flask).start()
    client.run(os.environ.get('DISCORD_TOKEN'))


    
