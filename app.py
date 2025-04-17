from flask import Flask, render_template, redirect, request, url_for, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
import yfinance as yf
from models import db, User, Portfolio
from werkzeug.security import generate_password_hash, check_password_hash

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

@app.route('/', methods=['GET', 'POST'])
def index():
    trades = []

    if current_user.is_authenticated:
        symbols = [p.symbol for p in Portfolio.query.filter_by(user_id=current_user.id).all()]
        if request.method == 'POST':
            symbol = request.form.get('symbol').upper()
            if symbol and symbol not in symbols:
                new_entry = Portfolio(symbol=symbol, user_id=current_user.id)
                db.session.add(new_entry)
                db.session.commit()
                symbols.append(symbol)
    else:
        if 'guest_symbols' not in session:
            session['guest_symbols'] = []

        if request.method == 'POST':
            symbol = request.form.get('symbol').upper()
            if symbol and symbol not in session['guest_symbols']:
                session['guest_symbols'].append(symbol)
                session.modified = True

        symbols = session.get('guest_symbols', [])

    for symbol in symbols:
        try:
            stock = yf.Ticker(symbol)
            data = stock.history(period="2d")
            if len(data) >= 2:
                current_price = data["Close"].iloc[-1]
                prev_close = data["Close"].iloc[-2]
                day_change = ((current_price - prev_close) / prev_close) * 100
            else:
                current_price = 0
                day_change = 0

            trades.append({
                "symbol": symbol,
                "course": round(current_price, 2),
                "day_change": round(day_change, 2)
            })
        except:
            continue

    return render_template('index.html', trades=trades)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('index'))
        else:
            return 'Invalid credentials'

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        # check if user already exists
        existing_user = User.query.filter_by(username=username).first()
        if existing_user:
            return "This username has already been used. Please choose another one."

        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
        new_user = User(username=username, password=hashed_pw)
        db.session.add(new_user)
        db.session.commit()
        login_user(new_user)
        return redirect(url_for('index'))

    return render_template('register.html')


@app.route('/delete/<symbol>', methods=['POST'])
def delete(symbol):
    symbol = symbol.upper()

    if current_user.is_authenticated:
        entry = Portfolio.query.filter_by(symbol=symbol, user_id=current_user.id).first()
        if entry:
            db.session.delete(entry)
            db.session.commit()
    else:
        if 'guest_symbols' in session:
            session['guest_symbols'] = [s for s in session['guest_symbols'] if s != symbol]
            session.modified = True

    return redirect(url_for('index'))


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

if __name__ == '__main__':
    print("Starting the app...")
    app.run(debug=True)
