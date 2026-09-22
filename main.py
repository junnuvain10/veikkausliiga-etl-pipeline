import os # Read environment variables such as database credentials and the API key.
import pandas as pd # Store and transform the standings data in tabular form.
import requests # Send the HTTP request to the football data API.
import psycopg2 # Connect to and interact with PostgreSQL.
from psycopg2 import sql # Build SQL statements and safely quote database identifiers.
from psycopg2.extras import execute_values # Insert multiple rows efficiently in one database operation.
from dotenv import load_dotenv # Load values from the local .env file into the process environment.


def run_etl():
    # Load the environment variables before reading configuration values.
    load_dotenv()

    # Read the API key used to authenticate the API request.
    API_KEY = os.getenv("API_KEY")
    # Select the Veikkausliiga competition and the season to download.
    LEAGUE_ID = 244
    SEASON = 2024

    # Read all PostgreSQL connection settings from the environment.
    DB_HOST = os.getenv("DB_HOST")
    DB_PORT = os.getenv("DB_PORT")
    DB_NAME = os.getenv("DB_NAME")
    DB_USER = os.getenv("DB_USER")
    DB_PASSWORD = os.getenv("DB_PASSWORD")
    # Name of the table that will contain the standings.
    TARGET_TABLE = 'standings'

    # Define the API endpoint, query parameters, and authentication header.
    url = "https://v3.football.api-sports.io/standings"
    params = {"league": LEAGUE_ID, "season": SEASON}
    headers = {"x-apisports-key": API_KEY}

    # Request the selected league standings and fail on an HTTP error response.
    response = requests.get(url=url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    # Decode the JSON response into Python dictionaries and lists.
    data = response.json()

    # Select the standings group returned for the requested league and season.
    standings_list = data["response"][0]["league"]["standings"][2]

    # Convert each club's API response into a flat row for the database table.
    rows = []
    for club in standings_list:
        # Extract the season and ranking position.
        season = SEASON
        position = club["rank"]
        # Extract the team's identity.
        team_id = club["team"]["id"]
        team = club["team"]["name"]
        # Extract the match record and goal statistics.
        played = club["all"]["played"]
        won = club["all"]["win"]
        draw = club["all"]["draw"]
        lost = club["all"]["lose"]
        goals_for = club["all"]["goals"]["for"]
        goals_against = club["all"]["goals"]["against"]
        goals_diff = club["goalsDiff"]
        points = club["points"]
        form = club["form"]

        # Keep the values in the same order as the database columns.
        rows.append(
            (season, position, team_id, team, played, won, draw, lost,
             goals_for, goals_against, goals_diff, points, form)
        )

    # Define the DataFrame and database column order.
    column_names = [
        'season', 'position', 'team_id', 'team',
        'played', 'won', 'draw', 'lost',
        'goals_for', 'goals_against', 'goals_diff',
        'points', 'form'
    ]

    # Create a DataFrame from the extracted rows.
    df = pd.DataFrame(rows, columns=column_names)
    # Make a standalone copy containing exactly the columns to be loaded.
    standings_df = df[column_names].copy()

    # Convert the DataFrame into tuples understood by psycopg2.
    standings_records_tuples = standings_df.itertuples(index=False, name=None)
    standings_records_tuples_to_list = list(standings_records_tuples)
    # Count the rows so the completion message reports the load size.
    num_of_rows_inserted = len(standings_records_tuples_to_list)

    try:
        # Stop before connecting if any required database setting is missing.
        if not all([DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD]):
            raise ValueError("Missing PostgreSQL environment variables. Check your .env file.")

        # Connect to the default postgres database to check the target database.
        connection = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname='postgres',
            user=DB_USER,
            password=DB_PASSWORD
        )

        # CREATE DATABASE must run outside a normal transaction.
        connection.autocommit = True
        cursor = connection.cursor()
        # Check whether the target database already exists.
        cursor.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (DB_NAME,)
        )
        # Create the target database only when it is not already present.
        if cursor.fetchone() is None:
            cursor.execute(sql.SQL("CREATE DATABASE {};").format(
                sql.Identifier(DB_NAME)
            ))
            print(f"Created PostgreSQL database {DB_NAME}.")
        # Close the administrative connection before reconnecting to the target database.
        cursor.close()
        connection.close()

        # Open a new connection to the database where the table belongs.
        connection = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        # Create a cursor for table creation and data insertion.
        cursor = connection.cursor()
        print(f"Connected to PostgreSQL {DB_NAME} database successfully.")

    # Report connection and database-creation errors, then stop the ETL run.
    except psycopg2.Error as e:
        print(f"Error connecting to PostgreSQL database: {e}")
        return


    # Create the destination table if it does not already exist.
    create_table_query = sql.SQL("""
        CREATE TABLE IF NOT EXISTS {} (
            season INT,
            position INT,
            team_id INT,
            team VARCHAR(255),
            played INT,
            won INT,
            draw INT,
            lost INT,
            goals_for INT,
            goals_against INT,
            goals_diff INT,
            points INT,
            form VARCHAR(255),
            loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """).format(sql.Identifier(TARGET_TABLE))
 

    try:
        # Send the table definition to PostgreSQL.
        cursor.execute(create_table_query)
        # Build a parameterized batch insert for all standings rows.
        insert_query = sql.SQL("""
            INSERT INTO {} (
                season, position, team_id, team, played, won, draw, lost,
                goals_for, goals_against, goals_diff, points, form
            ) VALUES %s
        """).format(sql.Identifier(TARGET_TABLE))
        # Insert the API data and commit both the table and data changes.
        execute_values(cursor, insert_query, standings_records_tuples_to_list)
        connection.commit()
        print(f"Table {TARGET_TABLE} created successfully. {num_of_rows_inserted} rows inserted.")
    # Roll back incomplete database work if table creation or insertion fails.
    except psycopg2.Error as e:
        print(f"Error creating table: {e}")
        connection.rollback()
    finally:
        # Always close the cursor and connection when database work is finished.
        if connection:
            cursor.close()
            connection.close()
            print("Database connection closed.")

    # A unique index can be added later to prevent duplicate team-season rows.
    # cursor.execute("""
    #     CREATE UNIQUE INDEX IF NOT EXISTS standings_season_team_id_uq
    #     ON standings (season, team_id)
    # """)

# Run the ETL process only when this file is executed directly.
if __name__ == "__main__":
    run_etl()
