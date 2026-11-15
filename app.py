import os, secrets, string, base64
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import or_

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", secrets.token_hex(32))
db_url = os.getenv("DATABASE_URL", "sqlite:///goko_store.db")
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg://", 1)
elif db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Devam etmek için giriş yapmalısın."

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    def set_password(self, password): self.password_hash = generate_password_hash(password)
    def check_password(self, password): return check_password_hash(self.password_hash, password)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, default="")
    price = db.Column(db.Float, nullable=False, default=0)
    image = db.Column(db.Text, default="")
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Coupon(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False)
    percent = db.Column(db.Integer, nullable=False, default=10)
    active = db.Column(db.Boolean, default=True)
    expires_at = db.Column(db.DateTime, nullable=True)

class StoreOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(16), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    customer_name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(40), nullable=False)
    address = db.Column(db.Text, default="")
    items = db.Column(db.Text, nullable=False)
    total = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(40), default="Sipariş alındı")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class SupportMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    sender_admin = db.Column(db.Boolean, default=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id): return db.session.get(User, int(user_id))

def admin_required(fn):
    @wraps(fn)
    @login_required
    def wrapper(*args, **kwargs):
        if not current_user.is_admin: abort(403)
        return fn(*args, **kwargs)
    return wrapper

def make_order_code():
    chars = string.ascii_uppercase + string.digits
    while True:
        code = "GOKO-" + "".join(secrets.choice(chars) for _ in range(7))
        if not StoreOrder.query.filter_by(code=code).first(): return code

@app.context_processor
def common_data():
    return {"cart_count": sum(int(v) for v in session.get("cart", {}).values())}

@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    query = Product.query.filter_by(active=True)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(Product.name.ilike(like), Product.description.ilike(like)))
    products = query.order_by(Product.created_at.desc()).all()
    return render_template("index.html", products=products, q=q)

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated: return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if len(username) < 3 or len(username) > 40:
            flash("Kullanıcı adı 3-40 karakter olmalı.", "error")
        elif len(password) < 8:
            flash("Şifre en az 8 karakter olmalı.", "error")
        elif User.query.filter_by(username=username).first():
            flash("Bu kullanıcı adı zaten kullanılıyor.", "error")
        else:
            user = User(username=username); user.set_password(password)
            db.session.add(user); db.session.commit()
            login_user(user); flash("Hesabın oluşturuldu!", "success")
            return redirect(url_for("index"))
    return render_template("auth.html", mode="register")

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated: return redirect(url_for("index"))
    if request.method == "POST":
        user = User.query.filter_by(username=request.form.get("username", "").strip()).first()
        if user and user.check_password(request.form.get("password", "")):
            login_user(user); flash("Giriş başarılı.", "success")
            return redirect(url_for("admin" if user.is_admin else "index"))
        flash("Kullanıcı adı veya şifre hatalı.", "error")
    return render_template("auth.html", mode="login")

@app.route("/logout")
@login_required
def logout():
    logout_user(); session.clear(); flash("Çıkış yapıldı.", "success")
    return redirect(url_for("index"))

@app.route("/cart")
def cart():
    cart_data = session.get("cart", {})
    products = []
    total = 0
    for pid, qty in cart_data.items():
        p = db.session.get(Product, int(pid))
        if p and p.active:
            qty = max(1, min(99, int(qty)))
            products.append((p, qty)); total += p.price * qty
    return render_template("cart.html", products=products, total=total)

@app.route("/cart/add/<int:product_id>", methods=["POST"])
def cart_add(product_id):
    p = db.session.get(Product, product_id)
    if not p or not p.active: abort(404)
    cart_data = session.get("cart", {})
    cart_data[str(product_id)] = min(99, int(cart_data.get(str(product_id), 0)) + 1)
    session["cart"] = cart_data
    flash("Ürün sepete eklendi.", "success")
    return redirect(request.referrer or url_for("index"))

@app.route("/cart/remove/<int:product_id>", methods=["POST"])
def cart_remove(product_id):
    cart_data = session.get("cart", {}); cart_data.pop(str(product_id), None)
    session["cart"] = cart_data
    return redirect(url_for("cart"))

@app.route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    cart_data = session.get("cart", {})
    lines, total = [], 0
    for pid, qty in cart_data.items():
        p = db.session.get(Product, int(pid))
        if p and p.active:
            qty = max(1, min(99, int(qty)))
            lines.append((p, qty)); total += p.price * qty
    if not lines:
        flash("Sepetin boş.", "error"); return redirect(url_for("index"))
    discount = 0; coupon_code = request.values.get("coupon", "").strip().upper()
    if coupon_code:
        c = Coupon.query.filter_by(code=coupon_code, active=True).first()
        if c and (not c.expires_at or c.expires_at > datetime.utcnow()):
            discount = min(90, max(0, c.percent)); total *= (100-discount)/100
        else: flash("Kupon geçersiz veya süresi dolmuş.", "error")
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        if not name or not phone:
            flash("Ad ve telefon zorunludur.", "error")
        else:
            details = " | ".join([f"{p.name} x{qty}" for p, qty in lines])
            order = StoreOrder(code=make_order_code(), user_id=current_user.id,
                               customer_name=name, phone=phone, address=address,
                               items=details, total=round(total, 2))
            db.session.add(order); db.session.commit()
            session["cart"] = {}
            flash(f"Sipariş oluşturuldu. Takip kodun: {order.code}", "success")
            return redirect(url_for("track", code=order.code))
    return render_template("checkout.html", lines=lines, total=round(total,2), discount=discount, coupon_code=coupon_code)

@app.route("/track", methods=["GET", "POST"])
def track():
    code = request.values.get("code", "").strip().upper()
    order = StoreOrder.query.filter_by(code=code).first() if code else None
    return render_template("track.html", order=order, code=code)

@app.route("/support", methods=["GET", "POST"])
@login_required
def support():
    if request.method == "POST":
        msg = request.form.get("message", "").strip()
        if msg:
            db.session.add(SupportMessage(user_id=current_user.id, message=msg[:2000]))
            db.session.commit(); flash("Mesajın destek ekibine gönderildi.", "success")
        return redirect(url_for("support"))
    messages = SupportMessage.query.filter_by(user_id=current_user.id).order_by(SupportMessage.created_at.asc()).all()
    return render_template("support.html", messages=messages)

@app.route("/admin")
@admin_required
def admin():
    return render_template("admin.html", products=Product.query.order_by(Product.id.desc()).all(),
                           orders=StoreOrder.query.order_by(StoreOrder.created_at.desc()).limit(50).all(),
                           coupons=Coupon.query.order_by(Coupon.id.desc()).all(),
                           users=User.query.order_by(User.id.desc()).all())

@app.route("/admin/product/save", methods=["POST"])
@admin_required
def product_save():
    pid = request.form.get("id", "").strip()
    p = db.session.get(Product, int(pid)) if pid else Product()
    if not p: abort(404)
    name = request.form.get("name", "").strip()
    try: price = float(request.form.get("price", "0").replace(",", "."))
    except ValueError: price = -1
    if not name or price < 0:
        flash("Ürün adı ve geçerli fiyat gerekli.", "error"); return redirect(url_for("admin"))
    p.name=name; p.description=request.form.get("description","").strip(); p.price=price
    p.image=request.form.get("image","").strip()
    p.active=bool(request.form.get("active"))
    if not pid: db.session.add(p)
    db.session.commit(); flash("Ürün kaydedildi.", "success")
    return redirect(url_for("admin"))

@app.route("/admin/product/delete/<int:pid>", methods=["POST"])
@admin_required
def product_delete(pid):
    p=db.session.get(Product,pid)
    if p: db.session.delete(p); db.session.commit(); flash("Ürün silindi.", "success")
    return redirect(url_for("admin"))

@app.route("/admin/order/<int:oid>", methods=["POST"])
@admin_required
def order_update(oid):
    order=db.session.get(StoreOrder,oid)
    if order:
        order.status=request.form.get("status","Sipariş alındı")[:40]
        db.session.commit(); flash("Sipariş durumu güncellendi.", "success")
    return redirect(url_for("admin"))

@app.route("/admin/coupon", methods=["POST"])
@admin_required
def coupon_add():
    code=request.form.get("code","").strip().upper()
    try: percent=int(request.form.get("percent","10"))
    except ValueError: percent=0
    if code and 1 <= percent <= 90 and not Coupon.query.filter_by(code=code).first():
        db.session.add(Coupon(code=code,percent=percent)); db.session.commit(); flash("Kupon eklendi.", "success")
    else: flash("Kupon kodu benzersiz olmalı; indirim %1-%90 arası olmalı.", "error")
    return redirect(url_for("admin"))

@app.route("/admin/coupon/delete/<int:cid>", methods=["POST"])
@admin_required
def coupon_delete(cid):
    c=db.session.get(Coupon,cid)
    if c: db.session.delete(c); db.session.commit()
    return redirect(url_for("admin"))

@app.route("/admin/support")
@admin_required
def admin_support():
    users = User.query.join(SupportMessage, User.id==SupportMessage.user_id).distinct().all()
    return render_template("admin_support.html", users=users)

@app.route("/admin/support/<int:uid>", methods=["GET","POST"])
@admin_required
def admin_support_chat(uid):
    user=db.session.get(User,uid)
    if not user: abort(404)
    if request.method=="POST":
        msg=request.form.get("message","").strip()
        if msg:
            db.session.add(SupportMessage(user_id=uid,sender_admin=True,message=msg[:2000])); db.session.commit()
    messages=SupportMessage.query.filter_by(user_id=uid).order_by(SupportMessage.created_at.asc()).all()
    return render_template("admin_chat.html", user=user, messages=messages)

@app.errorhandler(403)
def forbidden(e): return render_template("message.html", title="Yetki gerekli", message="Bu sayfaya erişim iznin yok."),403
@app.errorhandler(404)
def not_found(e): return render_template("message.html", title="Bulunamadı", message="Aradığın sayfa veya kayıt bulunamadı."),404

def initialize():
    with app.app_context():
        db.create_all()
        admin_name=os.getenv("ADMIN_USERNAME","admin")
        admin_password=os.getenv("ADMIN_PASSWORD","")
        if admin_password and not User.query.filter_by(username=admin_name).first():
            u=User(username=admin_name,is_admin=True); u.set_password(admin_password)
            db.session.add(u); db.session.commit()

initialize()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
