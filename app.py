import os
import csv
import io
from datetime import datetime
from functools import wraps

from flask import (Flask, render_template, request, redirect,
                   url_for, session, flash, Response)
from flask_sqlalchemy import SQLAlchemy
import random
from flask import jsonify

DRIVE_API_KEY = os.environ.get('GOOGLE_DRIVE_API_KEY')
DRIVE_FOLDER_ID = os.environ.get('GOOGLE_DRIVE_FOLDER_ID')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///wedding.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

GUEST_PASSWORD = os.environ.get('GUEST_PASSWORD', 'changeme')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'adminchangeme')

MEAL_OPTIONS_ADULT = ['Chicken', 'Fish', 'Vegetarian']
MEAL_OPTIONS_CHILD = ['Chicken', 'Fish', 'Vegetarian', 'Kids meal']

db = SQLAlchemy(app)


# ── Models ────────────────────────────────────────────────────────────────────

class Household(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(200), nullable=False)   # "The Zhangs"
    submitted_at = db.Column(db.DateTime, nullable=True)       # None = not yet responded
    guests       = db.relationship('Guest', backref='household',
                                   cascade='all, delete-orphan', lazy=True)

    @property
    def submitted(self):
        return self.submitted_at is not None

    @property
    def attending_count(self):
        return sum(1 for g in self.guests if g.attending)


class Guest(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    household_id = db.Column(db.Integer, db.ForeignKey('household.id'), nullable=False)
    name         = db.Column(db.String(200), nullable=False)
    is_child     = db.Column(db.Boolean, default=False)
    attending    = db.Column(db.Boolean, nullable=True)   # None = not yet answered
    meal_choice  = db.Column(db.String(50), nullable=True)


# ── Auth helpers ──────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == ADMIN_PASSWORD:
            session['logged_in'] = True
            session['admin'] = True
            return redirect(url_for('admin'))
        elif password == GUEST_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            flash("That password doesn't seem right - try again.")
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ── Public routes ─────────────────────────────────────────────────────────────


@app.route('/')
@login_required
def index():
    image_dir = os.path.join(app.static_folder, 'images', 'WeddingSite')
    try:
        images = [
            f"images/WeddingSite/{f}"
            for f in os.listdir(image_dir)
            if f.lower().endswith(('.jpg', '.jpeg', '.png', '.webp'))
        ]
        random.shuffle(images)
        images = images[:4]
    except FileNotFoundError:
        images = []
    return render_template('index.html', images=images)


@app.route('/schedule')
@login_required
def schedule():
    return render_template('schedule.html')

@app.route('/details')
@login_required
def details():
    return render_template('details.html')

@app.route('/rsvp', methods=['GET', 'POST'])
@login_required
def rsvp():
    if request.method == 'POST':
        search = request.form.get('name', '').strip()
        if not search:
            flash('Please enter your name.')
            return render_template('rsvp.html')

        guest = Guest.query.filter(
            db.func.lower(Guest.name) == search.lower(),
            Guest.is_child == False,
            ~Guest.name.startswith('+')
        ).first()

        if guest:
            return redirect(url_for('rsvp_form', hid=guest.household_id))

        household = Household.query.filter(
            db.func.lower(Household.name) == search.lower()
        ).first()

        if household:
            return redirect(url_for('rsvp_form', hid=household.id))

        flash("We couldn't find that name - check your invitation and try again.")
        return render_template('rsvp.html')

    return render_template('rsvp.html')


@app.route('/rsvp/<int:hid>', methods=['GET', 'POST'])
@login_required
def rsvp_form(hid):
    household = Household.query.get_or_404(hid)

    if request.method == 'POST':
        any_attending = False

        for guest in household.guests:
            # Allow +1 guests to set their real name
            new_name = request.form.get(f'name_{guest.id}', '').strip()
            attending_val = request.form.get(f'attending_{guest.id}')

            # +1 guests must be named if attending
            if guest.name.startswith('+') and attending_val == 'yes' and not new_name:
                flash('Please enter a name for your plus-one.')
                return render_template('rsvp_form.html', household=household,
                                    meal_options_adult=MEAL_OPTIONS_ADULT,
                                    meal_options_child=MEAL_OPTIONS_CHILD)

            if new_name:
                guest.name = new_name
            attending_val = request.form.get(f'attending_{guest.id}')
            guest.attending = (attending_val == 'yes')

            if guest.attending:
                any_attending = True
                options = MEAL_OPTIONS_CHILD if guest.is_child else MEAL_OPTIONS_ADULT
                meal = request.form.get(f'meal_{guest.id}', '').strip()
                if meal not in options:
                    flash(f'Please select a meal for {guest.name}.')
                    return render_template('rsvp_form.html', household=household,
                                           meal_options_adult=MEAL_OPTIONS_ADULT,
                                           meal_options_child=MEAL_OPTIONS_CHILD)
                guest.meal_choice = meal
            else:
                guest.meal_choice = None

        household.submitted_at = datetime.utcnow()
        db.session.commit()
        return redirect(url_for('rsvp_confirm', hid=household.id))

    return render_template('rsvp_form.html', household=household,
                           meal_options_adult=MEAL_OPTIONS_ADULT,
                           meal_options_child=MEAL_OPTIONS_CHILD)


@app.route('/rsvp/confirm/<int:hid>')
@login_required
def rsvp_confirm(hid):
    household = Household.query.get_or_404(hid)
    attending = any(g.attending for g in household.guests)
    return render_template('rsvp_confirm.html', household=household, attending=attending)


# ── Admin routes ──────────────────────────────────────────────────────────────

@app.route('/admin')
@admin_required
def admin():
    households = Household.query.order_by(Household.name).all()

    total_guests  = Guest.query.count()
    total_attend  = Guest.query.filter_by(attending=True).count()
    total_decline = Guest.query.filter_by(attending=False).count()

    # Meal counts split by adult / child
    meal_counts = {}
    for g in Guest.query.filter_by(attending=True).all():
        key = (g.meal_choice, 'Child' if g.is_child else 'Adult')
        meal_counts[key] = meal_counts.get(key, 0) + 1

    return render_template('admin.html',
                           households=households,
                           total_guests=total_guests,
                           total_attend=total_attend,
                           total_decline=total_decline,
                           meal_counts=meal_counts)


@app.route('/admin/households', methods=['POST'])
@admin_required
def add_household():
    name = request.form.get('household_name', '').strip()
    if not name:
        flash('Household name is required.')
        return redirect(url_for('admin'))

    household = Household(name=name)
    db.session.add(household)
    db.session.flush()  # get the id before adding guests

    i = 1
    while True:
        guest_name = request.form.get(f'guest_name_{i}', '').strip()
        if not guest_name:
            break
        is_child = request.form.get(f'guest_child_{i}') == 'on'
        household.guests.append(Guest(name=guest_name, is_child=is_child))
        i += 1

    db.session.commit()
    return redirect(url_for('admin'))


@app.route('/admin/households/<int:hid>/delete', methods=['POST'])
@admin_required
def delete_household(hid):
    household = Household.query.get_or_404(hid)
    db.session.delete(household)
    db.session.commit()
    return redirect(url_for('admin'))


@app.route('/admin/households/<int:hid>/guest', methods=['POST'])
@admin_required
def add_guest(hid):
    household = Household.query.get_or_404(hid)
    name = request.form.get('guest_name', '').strip()
    if name:
        is_child = request.form.get('is_child') == 'on'
        household.guests.append(Guest(name=name, is_child=is_child))
        db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/guests/<int:gid>/edit', methods=['POST'])
@admin_required
def edit_guest(gid):
    guest = Guest.query.get_or_404(gid)
    name = request.form.get('name', '').strip()
    if name:
        guest.name = name
    guest.is_child = request.form.get('is_child') == 'on'
    db.session.commit()
    return redirect(url_for('admin'))


@app.route('/admin/households/<int:hid>/edit', methods=['POST'])
@admin_required
def edit_household(hid):
    household = Household.query.get_or_404(hid)
    name = request.form.get('name', '').strip()
    if name:
        household.name = name
    db.session.commit()
    return redirect(url_for('admin'))

@app.route('/admin/guests/<int:gid>/delete', methods=['POST'])
@admin_required
def delete_guest(gid):
    guest = Guest.query.get_or_404(gid)
    db.session.delete(guest)
    db.session.commit()
    return redirect(url_for('admin'))


@app.route('/admin/export')
@admin_required
def export():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Household', 'Guest', 'Child', 'Attending', 'Meal', 'Submitted'])
    for h in Household.query.order_by(Household.name).all():
        for g in h.guests:
            writer.writerow([
                h.name,
                g.name,
                'Yes' if g.is_child else 'No',
                'Yes' if g.attending else ('No' if g.attending is False else 'Pending'),
                g.meal_choice or '',
                h.submitted_at.strftime('%Y-%m-%d %H:%M') if h.submitted_at else '',
            ])
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=rsvps.csv'},
    )


with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=False)
