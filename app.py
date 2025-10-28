from flask import Flask, request, jsonify, session, send_from_directory
from werkzeug.utils import secure_filename
from flask_cors import CORS
import os
import mysql.connector
from config import db_config

app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['UPLOAD_FOLDER'] = os.path.join('static', 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

CORS(app, supports_credentials=True, origins=["http://localhost:3000"])

def connect_db():
    return mysql.connector.connect(**db_config)

# ---------------- REGISTER ----------------
@app.route('/register', methods=['POST'])
def register():
    try:
        data = request.get_json()
        username = data.get('username')
        email = data.get('email')
        password = data.get('password')
        role = data.get('role', 'tenant')

        con = connect_db()
        cur = con.cursor()
        cur.execute(
            "INSERT INTO users (username, email, password, role) VALUES (%s, %s, %s, %s)",
            (username, email, password, role)
        )
        con.commit()
        con.close()

        return jsonify({"message": "Registration successful!"})
    except Exception as e:
        print("❌ Registration error:", e)
        return jsonify({"message": "Registration failed", "error": str(e)}), 400

# ---------------- LOGIN ----------------
@app.route('/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        username = data.get('username')
        password = data.get('password')

        con = connect_db()
        cur = con.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE username=%s AND password=%s", (username, password))
        user = cur.fetchone()
        con.close()

        if user:
            session['user'] = user
            return jsonify({"message": "Login successful!", "user": {"id": user["id"], "username": user["username"], "role": user["role"]}})
        else:
            return jsonify({"message": "Invalid username or password"}), 401
    except Exception as e:
        print("❌ Login error:", e)
        return jsonify({"message": "Login failed", "error": str(e)}), 400

# ---------------- LANDLORD POST PROPERTY ----------------
@app.route('/landlord', methods=['POST'])
def landlord():
    if 'user' not in session:
        return jsonify({"message": "Unauthorized"}), 401

    if session['user']['role'] != 'landlord':
        return jsonify({"message": "Access denied: Only landlords can post flats"}), 403

    try:
        data = request.form
        image = request.files['image']
        phone = data.get('phone', '')

        # Phone validation
        if not phone.isdigit() or len(phone) != 10:
            return jsonify({"message": "Invalid mobile number. Must be exactly 10 digits."}), 400

        image_path = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(image.filename))
        image.save(image_path)

        con = connect_db()
        cur = con.cursor()
        cur.execute("""
            INSERT INTO flats (landlord_id, name, phone, address, location_link, rent, facilities, image_path, is_rented) 
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,FALSE)
        """, (
            session['user']['id'],
            data['name'],
            data['phone'],
            data['address'],
            data['location_link'],
            data['rent'],
            data['facilities'],
            image_path
        ))
        con.commit()
        con.close()

        return jsonify({"message": "Flat listed successfully!"}), 200
    except Exception as e:
        print("❌ Landlord form error:", e)
        return jsonify({"message": "Something went wrong", "error": str(e)}), 500

# ---------------- TENANT VIEW PROPERTIES ----------------
@app.route('/tenant', methods=['GET', 'POST'])
def tenant():
    try:
        con = connect_db()
        cur = con.cursor(dictionary=True)

        if request.method == 'POST':
            location = request.form.get('location')
            max_rent = request.form.get('max_rent')
            query = "SELECT * FROM flats WHERE is_rented = FALSE"
            params = []

            if location:
                query += " AND address LIKE %s"
                params.append(f"%{location}%")
            if max_rent:
                query += " AND rent <= %s"
                params.append(max_rent)

            cur.execute(query, params)
        else:
            cur.execute("SELECT * FROM flats WHERE is_rented = FALSE")

        flats = cur.fetchall()
        con.close()

        # Add correct image URLs
        for f in flats:
            f['image_url'] = f"/static/uploads/{os.path.basename(f['image_path'])}" if f.get('image_path') else None
            # Ensure phone number is included
            f['contact'] = f.get('phone', 'N/A')

        return jsonify({"flats": flats})

    except Exception as e:
        print("❌ Tenant fetch error:", e)
        return jsonify({"message": "Error fetching flats", "error": str(e)}), 500

# ---------------- SERVE UPLOADED IMAGES ----------------
@app.route('/static/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ---------------- GET USER PROFILE ----------------
@app.route('/profile', methods=['GET'])
def profile():
    if 'user' in session:
        user = session['user']
        return jsonify({
            "id": user["id"],
            "username": user["username"],
            "email": user.get("email", ""),
            "phone": user.get("phone", ""),
            "address": user.get("address", ""),
            "role": user["role"]
        })
    else:
        return jsonify({"message": "Unauthorized"}), 401
    
# ---------------- DELETE ACCOUNT ----------------
@app.route('/delete_account', methods=['DELETE'])
def delete_account():
    if 'user' not in session:
        return jsonify({"message": "Unauthorized"}), 401

    user_id = session['user']['id']

    try:
        con = connect_db()
        cur = con.cursor()

        # 1️⃣ Delete all flats of landlord (if any)
        if session['user']['role'] == 'landlord':
            cur.execute("SELECT image_path FROM flats WHERE landlord_id = %s", (user_id,))
            flats = cur.fetchall()
            for f in flats:
                image_path = f[0]
                if image_path and os.path.exists(image_path):
                    os.remove(image_path)
            cur.execute("DELETE FROM flats WHERE landlord_id = %s", (user_id,))

        # 2️⃣ Delete the user
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        con.commit()
        con.close()

        # 3️⃣ Clear session
        session.pop('user', None)

        return jsonify({"message": "Your account has been deleted successfully."})

    except Exception as e:
        print("❌ Delete account error:", e)
        return jsonify({"message": "Failed to delete account", "error": str(e)}), 500


# ---------------- MY FLATS ----------------
@app.route('/myflats', methods=['GET'])
def my_flats():
    if 'user' not in session:
        return jsonify({"message": "Unauthorized"}), 401

    landlord_id = session['user']['id']
    con = connect_db()
    cur = con.cursor(dictionary=True)
    cur.execute("SELECT * FROM flats WHERE landlord_id = %s", (landlord_id,))
    flats = cur.fetchall()
    con.close()

    for f in flats:
        f['image_url'] = f"/static/uploads/{os.path.basename(f['image_path'])}"

    return jsonify({"flats": flats})

@app.route('/myflats/<int:flat_id>', methods=['PUT'])
def edit_flat(flat_id):
    if 'user' not in session:
        return jsonify({"message": "Unauthorized"}), 401

    data = request.json
    con = connect_db()
    cur = con.cursor()
    cur.execute("""
        UPDATE flats 
        SET name=%s, phone=%s, address=%s, location_link=%s, rent=%s, facilities=%s
        WHERE id=%s AND landlord_id=%s
    """, (
        data['name'], data['phone'], data['address'], data['location_link'],
        data['rent'], data['facilities'], flat_id, session['user']['id']
    ))
    con.commit()
    con.close()
    return jsonify({"message": "Flat updated successfully"})

@app.route('/myflats/<int:flat_id>', methods=['DELETE'])
def delete_flat(flat_id):
    if 'user' not in session:
        return jsonify({"message": "Unauthorized"}), 401

    con = connect_db()
    cur = con.cursor(dictionary=True)
    cur.execute("SELECT image_path FROM flats WHERE id=%s AND landlord_id=%s", (flat_id, session['user']['id']))
    row = cur.fetchone()
    if not row:
        return jsonify({"message": "Flat not found"}), 404

    image_path = row['image_path']
    cur.execute("DELETE FROM flats WHERE id=%s AND landlord_id=%s", (flat_id, session['user']['id']))
    con.commit()
    con.close()

    if os.path.exists(image_path):
        os.remove(image_path)

    return jsonify({"message": "Flat deleted successfully"})

if __name__ == '__main__':
    app.run(debug=True, port=5000)


# in app.py
@app.route("/")
def home():
    return "FlatFinder Backend is live!"
