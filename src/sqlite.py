import sqlite3

def query_videos():
    conn = sqlite3.connect('tt_metadata.db')
    c = conn.cursor()
    
    # Example queries
    print("\n=== Most Recent Videos ===")
    c.execute('''
        SELECT title, channel, upload_date, view_count 
        FROM videos 
        ORDER BY upload_date DESC 
        LIMIT 5
    ''')
    for row in c.fetchall():
        print(f"Title: {row[0]}")
        print(f"Channel: {row[1]}")
        print(f"Upload Date: {row[2]}")
        print(f"Views: {row[3]:,}")
        print("-" * 50)
    
    print("\n=== Video Format Statistics ===")
    c.execute('''
        SELECT v.title, COUNT(f.id) as format_count,
               MAX(CASE 
                   WHEN f.resolution != 'N/A' 
                   THEN f.resolution 
                   END) as max_resolution
        FROM videos v
        LEFT JOIN formats f ON v.video_id = f.video_id
        GROUP BY v.video_id
    ''')
    for row in c.fetchall():
        print(f"Title: {row[0]}")
        print(f"Available Formats: {row[1]}")
        print(f"Max Resolution: {row[2]}")
        print("-" * 50)
    
    conn.close()

# If you want to run a custom query
def custom_query(query):
    conn = sqlite3.connect('tt_metadata.db')
    c = conn.cursor()
    
    try:
        c.execute(query)
        results = c.fetchall()
        # Get column names
        column_names = [description[0] for description in c.description]
        
        # Print column names
        print("\n" + " | ".join(column_names))
        print("-" * 50)
        
        # Print results
        for row in results:
            print(" | ".join(str(item) for item in row))
            
    except sqlite3.Error as e:
        print(f"Error executing query: {e}")
    
    conn.close()

if __name__ == "__main__":
    # Run preset queries
    # query_videos()
    
    # Example of running a custom query
    custom_query("""
        SELECT count(*) 
        FROM videos
    """)