import streamlit as st
import pdfplumber
import pandas as pd
import re

st.set_page_config(page_title="Invoice → CSV", page_icon="📄", layout="centered")

st.title("📄 Invoice to CSV")
st.write("Upload invoice PDF, get tracking CSV")

# Map PDF stage names to output columns
STAGE_TO_COLUMN = {
    'חתימה על החוזה': 'עד זכיה',
    'מצגת דיירים': 'עד זכיה',
    'תוכניות דירות תמורה': 'בחירת יזם',
    'התכנון הראשוני': 'בחירת יזם',
    'הגשת ההיתר': '51% חתימות',
    'החלטת וועדה': '67% חתימות',
    'חשבון אגרות': 'לאחר שנה מ67%',
    'למכרז': 'לאחר שנתיים',
    'לביצוע': 'היתר',
    'טופס 4': 'סה"כ',
}

# Output columns matching the yellow header
COLS = [
    'שלב תכנון',           # Stage name from PDF
    'שם/חברה נבחרת',       # Company name
    'דירוג שירות 1-10',    # Service rating (empty)
    'דירוג מחיר טוב 1-10', # Price rating (empty)
    'היקף חוזה',           # Contract scope (total contract amount)
    'עד זכיה',             # Until winning
    'בחירת יזם',           # Developer selection
    '51% חתימות',          # 51% signatures
    '67% חתימות',          # 67% signatures
    'לאחר שנה מ67%',       # After year from 67%
    'לאחר שנתיים',         # After 2 years
    'היתר',                # Permit
    'סה"כ',                # Total
]

MILESTONE_COLS = ['עד זכיה', 'בחירת יזם', '51% חתימות', '67% חתימות', 
                  'לאחר שנה מ67%', 'לאחר שנתיים', 'היתר', 'סה"כ']


def parse_num(s):
    """Parse number from string, handling commas and Hebrew formatting."""
    if s is None:
        return 0.0
    try:
        cleaned = str(s).replace(',', '').replace('₪', '').replace('%', '').strip()
        return float(cleaned) if cleaned else 0.0
    except:
        return 0.0


def fix_hebrew(s):
    """Reverse Hebrew string if it appears backwards (RTL extraction issue)."""
    if not s:
        return s
    text = str(s).strip()
    
    # Remove newlines - replace with space
    text = text.replace('\n', ' ').replace('\r', ' ')
    text = ' '.join(text.split())  # Normalize whitespace
    
    # Check if text looks reversed by looking for backwards Hebrew patterns
    # Common reversed patterns from PDF extraction:
    # םע = עם (with), בלש = שלב (stage), הזוח = חוזה (contract)
    # םוכס = סכום (amount), רבטצמ = מצטבר (cumulative)
    reversed_patterns = ['םע', 'בלש', 'הזוח', 'םוכס', 'רבטצמ', 'ןובשח', 'עוציב']
    
    if any(p in text for p in reversed_patterns):
        return text[::-1]
    return text


def extract_company(text):
    """Extract company name from PDF text."""
    # Check for KOT (various spellings/reversals)
    kot_patterns = ['קיי.או.טי', 'קי.או.טי', 'KOT', 'יט.וא.ייק', 'יט.וא.יק']
    for p in kot_patterns:
        if p in text or p in text.upper():
            return 'קיי.או.טי אדריכלים'
    
    if 'ירון אליאב' in text or 'באילא ןורי' in text:
        return 'ירון אליאב'
    
    # Try to find company near "עוסק מורשה" or other patterns
    return 'Unknown'


def extract_vat_total(text):
    """Extract total with VAT."""
    # Look for "סה"כ (כולל מע"מ) לתשלום" pattern
    patterns = [
        r'לתשלום\s*([\d,]+\.?\d*)\s*₪',
        r'₪\s*([\d,]+\.?\d*)\s*לתשלום',
        r'סה.כ.*?לתשלום.*?([\d,]+\.?\d*)',
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return parse_num(m.group(1))
    return 0.0


def get_milestone_column(stage_name):
    """Map a stage name to its output column."""
    if not stage_name:
        return None
    
    # Try matching with the fixed stage name
    for pattern, col in STAGE_TO_COLUMN.items():
        if pattern in stage_name:
            return col
    
    # Also try with reversed pattern (in case text wasn't reversed properly)
    for pattern, col in STAGE_TO_COLUMN.items():
        if pattern[::-1] in stage_name:
            return col
    
    return None


def extract(pdf_file):
    """Extract all data from the PDF - each row is a phase with ALL its fields."""
    data = {
        'company': '',
        'contract_total': 0,
        'billed_total': 0,
        'vat_total': 0,
        'phases': [],       # Each phase = one row with ALL columns
        'headers': [],      # Column headers from the table
    }
    
    with pdfplumber.open(pdf_file) as pdf:
        full_text = ''
        all_tables = []
        
        for page in pdf.pages:
            page_text = page.extract_text() or ''
            full_text += page_text + '\n'
            
            tables = page.extract_tables()
            all_tables.extend(tables)
        
        # Extract company
        data['company'] = extract_company(full_text)
        
        # Extract VAT total
        data['vat_total'] = extract_vat_total(full_text)
        
        # Find the milestone table and extract ALL columns for each phase
        for table in all_tables:
            if not table or len(table) < 2:
                continue
            
            header = table[0]
            if not header or len(header) < 4:
                continue
            
            # Check if this looks like the milestone table
            header_text = ' '.join(str(c) for c in header if c)
            if 'שלב' not in header_text and 'בלש' not in header_text:
                continue
            
            # Fix Hebrew in headers and store them
            data['headers'] = [fix_hebrew(str(h)) if h else f'col_{i}' for i, h in enumerate(header)]
            
            for row in table[1:]:
                if not row:
                    continue
                
                # Get raw row text to check for summary rows
                row_text = ' '.join(str(c) for c in row if c)
                
                # Skip summary rows
                skip_words = ['סכום כולל', 'ללוכ םוכס', 'סכום מצטבר', 'רבטצמ םוכס', 'סה"כ']
                if any(word in row_text for word in skip_words):
                    continue
                
                # Skip empty rows
                if not any(row):
                    continue
                
                # Create phase dict with ALL fields from this row
                phase = {}
                for i, cell in enumerate(row):
                    col_name = data['headers'][i] if i < len(data['headers']) else f'col_{i}'
                    # Clean cell value - remove newlines, fix Hebrew
                    raw_val = str(cell) if cell else ''
                    # Remove newlines from raw value
                    raw_val = raw_val.replace('\n', ' ').replace('\r', ' ')
                    raw_val = ' '.join(raw_val.split())  # Normalize whitespace
                    fixed_val = fix_hebrew(raw_val)
                    
                    # Try to parse as number if it looks like one
                    num_val = parse_num(raw_val)
                    if num_val != 0 or raw_val.strip() in ['0', '0.0', '0.00']:
                        phase[col_name] = num_val
                    else:
                        phase[col_name] = fixed_val
                
                # Also store stage name for reference (last column typically)
                stage_col = data['headers'][-1] if data['headers'] else 'stage'
                phase['_stage'] = phase.get(stage_col, '')
                
                data['phases'].append(phase)
        
        # Calculate totals from phases
        if data['phases']:
            # Find the contract amount column (usually "סכום")
            contract_col = None
            billed_col = None
            for h in data['headers']:
                if 'סכום' in h and 'חשבון' not in h and 'מצטבר' not in h:
                    contract_col = h
                if 'חשבון זה' in h or 'בחשבון' in h:
                    billed_col = h
            
            # First column is usually billed amount
            if not billed_col and data['headers']:
                billed_col = data['headers'][0]
            
            for p in data['phases']:
                if contract_col and p.get(contract_col):
                    data['contract_total'] = p[contract_col]
                if billed_col:
                    val = p.get(billed_col, 0)
                    if isinstance(val, (int, float)):
                        data['billed_total'] += val
    
    return data


def to_tracking_rows(data):
    """Convert extracted phases to output rows - ALL fields per phase."""
    rows = []
    headers = data.get('headers', [])
    
    for phase in data['phases']:
        row = {}
        
        # Add all fields from the phase
        for h in headers:
            row[h] = phase.get(h, '')
        
        # Add company name
        row['שם/חברה נבחרת'] = data['company']
        
        rows.append(row)
    
    return rows, headers


# FILE UPLOAD
uploaded = st.file_uploader("Upload PDF", type=['pdf'], label_visibility="collapsed")

if uploaded:
    with st.spinner('מעבד...'):
        data = extract(uploaded)
        rows, headers = to_tracking_rows(data)
        
        # Build column order: all PDF headers + company name
        all_cols = headers + ['שם/חברה נבחרת'] if headers else ['שם/חברה נבחרת']
        df = pd.DataFrame(rows, columns=all_cols) if rows else pd.DataFrame()
    
    if rows:
        # Show summary
        st.success(f"✅ {data['company']} — {len(rows)} שלבים (כל השדות)")
        
        col1, col2, col3 = st.columns(3)
        col1.metric("היקף חוזה", f"₪{data['contract_total']:,.0f}")
        col2.metric("חיוב נוכחי", f"₪{data['billed_total']:,.0f}")
        col3.metric("כולל מע״מ", f"₪{data['vat_total']:,.0f}")
        
        # Show headers extracted
        with st.expander("🔍 עמודות שזוהו"):
            st.write(f"**{len(headers)} עמודות:** {', '.join(headers)}")
            st.write("---")
            for i, phase in enumerate(data['phases']):
                st.write(f"**שלב {i+1}:** {phase.get('_stage', 'N/A')}")
                for h in headers:
                    val = phase.get(h, '')
                    st.write(f"  • {h}: {val}")
        
        # Show table with ALL columns
        st.dataframe(df, use_container_width=True)
        
        # Download button
        csv = df.to_csv(index=False, encoding='utf-8-sig')
        st.download_button(
            "⬇️ Download CSV",
            csv,
            f"{uploaded.name.replace('.pdf', '')}_tracking.csv",
            "text/csv",
            use_container_width=True
        )
    else:
        st.warning("⚠️ לא נמצאו שלבים בקובץ PDF")
        
        # Debug: show raw tables
        with st.expander("🔍 Debug: Raw tables"):
            with pdfplumber.open(uploaded) as pdf:
                for i, page in enumerate(pdf.pages):
                    st.write(f"**Page {i+1}**")
                    tables = page.extract_tables()
                    for j, table in enumerate(tables):
                        st.write(f"Table {j+1}:")
                        st.dataframe(pd.DataFrame(table))
