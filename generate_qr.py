import qrcode
import os

def create_student_qr(student_id, student_name=None, output_dir="student_badges"):
    """
    Generates a high-contrast, durable QR code containing the student's ID string.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    qr = qrcode.QRCode(
        version=1,
        # Error correction 'H' handles up to 30% damage/wear on paper cards
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    
    # Store ONLY the raw student ID in the QR code
    qr.add_data(student_id)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")
    
    file_name = f"{student_id}_badge.png"
    file_path = os.path.join(output_dir, file_name)
    img.save(file_path)
    
    print(f"[Success] Generated QR badge for '{student_id}' at: {file_path}")
    return file_path

if __name__ == "__main__":
    # Example: Ensure the ID matches the ID registered via register_student.py
    create_student_qr("Akshaya", student_name="Akshaya")
