
import os
import mysql.connector

try:
    connection = mysql.connector.connect(
        host="127.0.0.1",
        port=3306,
        user="billing_app",
        password=os.environ["MEQETA_DB_PASSWORD"],
        database="meqeta_billing"
    )

    if connection.is_connected():
        print("Database connected successfully!")

        cursor = connection.cursor()
        cursor.execute("SHOW TABLES")

        for table in cursor.fetchall():
            print(table[0])

        cursor.close()
        connection.close()

except mysql.connector.Error as error:
    print("Database connection failed:", error)
