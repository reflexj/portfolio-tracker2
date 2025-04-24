from flask import Flask, render_template, redirect, request, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import yfinance as yf
from models import db, User, Portfolio, Cash
import csv
from io import TextIOWrapper
from flask import request

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///db.sqlite3'

db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

with app.app_context():
    db.create_all()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def deposit_cash(amount_chf):
    if amount_chf <= 0:
        return
    existing = Cash.query.filter_by(user_id=current_user.id, currency="CHF").first()
    if existing:
        existing.amount += amount_chf
    else:
        new_cash = Cash(user_id=current_user.id, currency="CHF", amount=amount_chf)
        db.session.add(new_cash)
    db.session.commit()

def subtract_cash(amount_chf):
    if amount_chf <= 0:
        return False

    cash = Cash.query.filter_by(user_id=current_user.id, currency="CHF").first()
    if not cash or cash.amount < amount_chf:
        return False  # Not enough cash
    cash.amount -= amount_chf
    db.session.commit()
    return True

@app.route('/', methods=['GET', 'POST'])
def index():
    trades = []
    totals = {"market_value": 0, "cost": 0, "realized_gain": 0, "day_gain": 0, "unrealized_gain": 0}

    if current_user.is_authenticated:
        if request.method == 'POST':
            symbol = request.form.get('symbol').upper()
            shares = float(request.form.get('shares'))
            avg_cost = float(request.form.get('avg_cost'))

            total_cost = shares * avg_cost
            if not subtract_cash(total_cost):
                flash("Not enough CHF cash to buy this stock.", "warning")
                return redirect(url_for('index'))

            existing = Portfolio.query.filter_by(symbol=symbol, user_id=current_user.id).first()
            if existing:
                total_shares = existing.shares + shares
                new_avg_cost = ((existing.avg_cost * existing.shares) + (avg_cost * shares)) / total_shares
                existing.shares = total_shares
                existing.avg_cost = new_avg_cost
            else:
                db.session.add(Portfolio(symbol=symbol, shares=shares, avg_cost=avg_cost, user_id=current_user.id))
            db.session.commit()

        positions = Portfolio.query.filter_by(user_id=current_user.id).all()

        for pos in positions:
            try:
                ticker = yf.Ticker(pos.symbol)
                info = ticker.info
                currency = info.get("currency", "CHF")

                fx_data = yf.Ticker(f"{currency}CHF=X").history(period="1d")
                fx_rate = float(fx_data["Close"].iloc[-1]) if currency != "CHF" else 1.0

                data = ticker.history(period="2d")
                current_price = float(data["Close"].iloc[-1])
                prev_price = float(data["Close"].iloc[-2])

                market_value_chf = pos.shares * current_price * fx_rate
                cost_value_chf = pos.shares * pos.avg_cost * fx_rate
                day_gain = pos.shares * (current_price - prev_price) * fx_rate
                unrealized_gain = market_value_chf - cost_value_chf

                trades.append({
                    "id": pos.id,
                    "symbol": pos.symbol,
                    "shares": pos.shares,
                    "avg_cost": round(pos.avg_cost, 2),
                    "currency": currency,
                    "current_price": round(current_price, 2),
                    "market_value": round(market_value_chf, 2),
                    "total_cost": round(cost_value_chf, 2),
                    "day_gain_percent": round(((current_price - prev_price) / prev_price) * 100, 2),
                    "day_gain": round(day_gain, 2),
                    "unrealized_gain_percent": round(((market_value_chf - cost_value_chf) / cost_value_chf) * 100, 2) if cost_value_chf > 0 else 0,
                    "unrealized_gain": round(unrealized_gain, 2),
                    "realized_gain_percent": round(pos.realized_gain_percent, 2),
                    "realized_gain": round(pos.realized_gain, 2)
                })

                totals["market_value"] += market_value_chf
                totals["cost"] += cost_value_chf
                totals["day_gain"] += day_gain
                totals["unrealized_gain"] += unrealized_gain
                totals["realized_gain"] += pos.realized_gain

            except Exception as e:
                continue

        # Cash handling
        cash_entries = Cash.query.filter_by(user_id=current_user.id).all()
        cash_balances = []
        total_cash_chf = 0

        for c in cash_entries:
            amount = c.amount
            currency = c.currency

            if currency != "CHF":
                try:
                    fx_data = yf.Ticker(f"{currency}CHF=X").history(period="1d")
                    rate = float(fx_data["Close"].iloc[-1])
                    converted = amount * rate
                except:
                    rate = 1
                    converted = 0
            else:
                rate = 1
                converted = amount

            cash_balances.append({
                "currency": currency,
                "amount": round(amount, 2),
                "rate": round(rate, 4),
                "converted": round(converted, 2)
            })
            total_cash_chf += converted

        totals["market_value"] += total_cash_chf

        total_change_percent = (totals["unrealized_gain"] / totals["cost"]) * 100 if totals["cost"] > 0 else 0
        total_day_percent = (totals["day_gain"] / totals["market_value"]) * 100 if totals["market_value"] > 0 else 0

        return render_template('index.html', trades=trades,
            total_market_value=round(totals["market_value"], 2),
            total_unrealized_gain=round(totals["unrealized_gain"], 2),
            total_unrealized_gain_percent=round(total_change_percent, 2),
            total_realized_gain=round(totals["realized_gain"], 2),
            total_day_gain=round(totals["day_gain"], 2),
            total_day_gain_percent=round(total_day_percent, 2),
            cash_balances=cash_balances
        )

    return render_template('index.html', trades=[], cash_balances=[])


@app.route('/withdraw_cash', methods=['POST'])
@login_required
def withdraw_cash():
    amount = float(request.form.get('withdraw_amount'))
    currency = request.form.get('withdraw_currency')

    if amount <= 0 or not currency:
        flash("Invalid withdrawal.","error")
        return redirect(url_for('index'))

    cash = Cash.query.filter_by(user_id=current_user.id, currency=currency).first()
    if not cash or cash.amount < amount:
        flash(f"Not enough {currency} to withdraw.","error")
        return redirect(url_for('index'))

    cash.amount -= amount
    db.session.commit()
    flash(f"{amount} {currency} withdrawn from your cash holdings.","success")
    return redirect(url_for('index'))

@app.route('/sell/<int:position_id>', methods=['POST'])
@login_required
def sell(position_id):
    pos = Portfolio.query.filter_by(id=position_id, user_id=current_user.id).first()
    if not pos:
        flash("Position not found.", "error")
        return redirect(url_for('index'))

    sell_shares = float(request.form.get('sell_shares'))
    sell_price = float(request.form.get('sell_price'))

    if sell_shares > pos.shares:
        flash("You can't sell more than you own.", "warning")
        return redirect(url_for('index'))

    gain = (sell_price - pos.avg_cost) * sell_shares
    gain_percent = ((sell_price - pos.avg_cost) / pos.avg_cost * 100) if pos.avg_cost > 0 else 0

    original_shares = pos.shares
    pos.shares -= sell_shares

    # Add CHF cash
    total_sale = sell_price * sell_shares
    deposit_cash(total_sale)

    # Always update realized gain
    pos.realized_gain += gain
    if original_shares > 0:
        pos.realized_gain_percent += gain_percent * (sell_shares / original_shares)

    if pos.shares <= 0:
        pos.shares = 0  # Mark as closed instead of deleting
        flash(f"Position geschlossen. Realisierter Gewinn: {round(gain, 2)} CHF", "success")
    else:
        flash(f"{sell_shares} Anteile verkauft. Gewinn: {round(gain, 2)} CHF", "success")

    db.session.commit()
    return redirect(url_for('index'))

def get_exchange_rate(from_currency, to_currency="CHF"):
    if from_currency == to_currency:
        return 1.0
    try:
        ticker = f"{from_currency}{to_currency}=X"
        data = yf.Ticker(ticker).history(period="1d")
        return float(data["Close"].iloc[-1])
    except:
        return None


@app.route('/add_cash', methods=['POST'])
@login_required
def add_cash():
    amount = float(request.form.get('cash_amount'))
    currency = request.form.get('cash_currency')

    if amount <= 0 or not currency:
        flash("Invalid cash deposit.","error")
        return redirect(url_for('index'))

    existing = Cash.query.filter_by(user_id=current_user.id, currency=currency).first()
    if existing:
        existing.amount += amount
    else:
        db.session.add(Cash(user_id=current_user.id, currency=currency, amount=amount))

    db.session.commit()
    flash(f"{amount} {currency} added to your cash holdings.","success")
    return redirect(url_for('index'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('index'))
        flash("Invalid credentials","error")
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if User.query.filter_by(username=username).first():
            flash("Username already exists.","warning")
        else:
            user = User(username=username, password=generate_password_hash(password, method='pbkdf2:sha256'))
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for('index'))
    return render_template('register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

@app.route('/import_csv', methods=['POST'])
@login_required
def import_csv():
    file = request.files.get('csv_file')

    if not file:
        flash("No file selected.", "error")
        return redirect(url_for('index'))

    def get_currency_from_symbol(symbol):
        try:
            info = yf.Ticker(symbol).info
            return info.get("currency", "CHF")
        except:
            return "CHF"

    try:
        stream = TextIOWrapper(file.stream, encoding='utf-8')
        reader = csv.DictReader(stream)
        imported_count = 0
        skipped_rows = []

        for i, row in enumerate(reader, start=2):  # Header is row 1
            try:
                symbol = row['Symbol'].strip().upper()
                shares = float(row['Quantity'])
                avg_cost = float(row['Purchase Price'])

                # Optional fields for realized gain
                realized_gain = float(row.get('Realized Gain', 0.0) or 0.0)
                realized_gain_percent = float(row.get('Realized Gain (%)', 0.0) or 0.0)

                # Skip if symbol is missing or if both shares and gain are 0
                if not symbol or (shares <= 0 and realized_gain == 0):
                    skipped_rows.append(i)
                    continue

                currency = get_currency_from_symbol(symbol)

                existing = Portfolio.query.filter_by(user_id=current_user.id, symbol=symbol).first()
                if existing:
                    if shares > 0:
                        total_shares = existing.shares + shares
                        new_avg_cost = ((existing.avg_cost * existing.shares) + (avg_cost * shares)) / total_shares
                        existing.shares = total_shares
                        existing.avg_cost = new_avg_cost
                    existing.realized_gain += realized_gain
                    existing.realized_gain_percent += realized_gain_percent
                    existing.currency = currency  # Update currency if needed
                else:
                    new_position = Portfolio(
                        user_id=current_user.id,
                        symbol=symbol,
                        shares=shares,
                        avg_cost=avg_cost,
                        realized_gain=realized_gain,
                        realized_gain_percent=realized_gain_percent,
                        currency=currency
                    )
                    db.session.add(new_position)

                imported_count += 1
            except Exception:
                skipped_rows.append(i)

        db.session.commit()

        if imported_count:
            flash(f"✅ Imported {imported_count} positions from CSV.", "success")
        if skipped_rows:
            flash(f"⚠️ Skipped rows with invalid or missing data: {', '.join(map(str, skipped_rows))}", "warning")

    except Exception as e:
        flash(f"❌ Failed to import CSV: {e}", "error")

    return redirect(url_for('index'))


def delete_all_user_data(user_id):
    # Lösche Portfolioeinträge
    Portfolio.query.filter_by(user_id=user_id).delete()

    # Lösche Cash-Bestände
    Cash.query.filter_by(user_id=user_id).delete()

    # Optional: User selbst löschen
    # User.query.filter_by(id=user_id).delete()

    db.session.commit()
@app.route('/delete_my_data', methods=['POST'])
@login_required
def delete_my_data():
    delete_all_user_data(current_user.id)
    flash("Deine Daten wurden gelöscht.", "success")
    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(debug=True)
