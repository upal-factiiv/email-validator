import re
import socket
import smtplib
from email.mime.text import MIMEText
import dns.resolver
from typing import Tuple, Dict, List
from fastapi import FastAPI, HTTPException, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, EmailStr
import uvicorn
import os
import pandas as pd
import io
import uuid
from datetime import datetime
from sqlalchemy.orm import Session
from database import EmailValidation, create_tables, get_db

class EmailValidator:
    def __init__(self):
        # RFC 5322 compliant regex pattern
        self.email_pattern = re.compile(
            r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        )
        
    def validate_format(self, email: str) -> bool:
        """Basic format validation"""
        if not email or '@' not in email:
            return False
        
        # Check basic pattern
        if not self.email_pattern.match(email):
            return False
            
        local, domain = email.rsplit('@', 1)
        
        # Local part validations
        if len(local) > 64 or len(domain) > 253:
            return False
            
        # Domain part validations
        if domain.startswith('.') or domain.endswith('.'):
            return False
            
        if '..' in domain:
            return False
            
        return True
    
    def check_mx_record(self, domain: str) -> bool:
        """Check if domain has MX records"""
        try:
            mx_records = dns.resolver.resolve(domain, 'MX')
            return len(mx_records) > 0
        except:
            return False
    
    def check_smtp_deliverability(self, email: str) -> Tuple[bool, str]:
        """Check SMTP deliverability (most comprehensive)"""
        if not self.validate_format(email):
            return False, "Invalid email format"
        
        domain = email.split('@')[1]
        
        # Check MX records first
        if not self.check_mx_record(domain):
            return False, "No MX records found"
        
        try:
            # Get MX records
            mx_records = dns.resolver.resolve(domain, 'MX')
            mx_record = str(mx_records[0].exchange)
            
            # Connect to SMTP server
            server = smtplib.SMTP(timeout=10)
            server.connect(mx_record, 25)
            server.helo('test.com')  # Use your domain here
            server.mail('test@test.com')  # Use your email here
            
            # Test the specific email
            code, message = server.rcpt(email)
            server.quit()
            
            # 250 means email is valid and deliverable
            if code == 250:
                return True, "Email is deliverable"
            else:
                return False, f"Email not deliverable: {message.decode()}"
                
        except Exception as e:
            return False, f"SMTP check failed: {str(e)}"
    
    def comprehensive_validate(self, email: str) -> Dict:
        """Complete validation with all checks"""
        result = {
            'email': email,
            'format_valid': False,
            'mx_valid': False,
            'deliverable': False,
            'message': ''
        }
        
        # Format check
        if not self.validate_format(email):
            result['message'] = "Invalid email format"
            return result
        result['format_valid'] = True
        
        # MX record check
        domain = email.split('@')[1]
        if not self.check_mx_record(domain):
            result['message'] = "Domain has no MX records"
            return result
        result['mx_valid'] = True
        
        # SMTP deliverability check
        is_deliverable, message = self.check_smtp_deliverability(email)
        result['deliverable'] = is_deliverable
        result['message'] = message
        
        return result

# Pydantic models for request/response
class EmailRequest(BaseModel):
    email: str

class EmailValidationResponse(BaseModel):
    email: str
    format_valid: bool
    mx_valid: bool
    deliverable: bool
    message: str
    validated_at: datetime = None

class BatchValidationResponse(BaseModel):
    batch_id: str
    total_emails: int
    deliverable_count: int
    non_deliverable_count: int
    results: List[EmailValidationResponse]

class EmailRecord(BaseModel):
    id: int
    email: str
    format_valid: bool
    mx_valid: bool
    deliverable: bool
    message: str
    validated_at: datetime
    batch_id: str = None
    
    class Config:
        from_attributes = True

# FastAPI app
app = FastAPI(
    title="Email Validator API",
    description="A comprehensive email validation API that checks format, MX records, and SMTP deliverability with database storage and batch processing",
    version="2.0.0"
)

# Initialize database
create_tables()

# Initialize validator
validator = EmailValidator()

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the HTML interface"""
    try:
        with open("index.html", "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content, status_code=200)
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>Email Validator API</h1><p>HTML interface not found. Please check if index.html exists.</p>",
            status_code=200
        )

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy"}

@app.post("/validate", response_model=EmailValidationResponse)
async def validate_email(request: EmailRequest, db: Session = Depends(get_db)):
    """
    Validate an email address comprehensively
    
    This endpoint performs:
    - Format validation (RFC 5322 compliant)
    - MX record check
    - SMTP deliverability test
    - Saves result to database
    """
    try:
        result = validator.comprehensive_validate(request.email)
        
        # Save to database
        db_validation = EmailValidation(
            email=result['email'],
            format_valid=result['format_valid'],
            mx_valid=result['mx_valid'],
            deliverable=result['deliverable'],
            message=result['message'],
            validated_at=datetime.utcnow()
        )
        db.add(db_validation)
        db.commit()
        db.refresh(db_validation)
        
        # Add timestamp to response
        result['validated_at'] = db_validation.validated_at
        
        return EmailValidationResponse(**result)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Validation error: {str(e)}")

@app.get("/deliverable-emails")
async def get_deliverable_emails(db: Session = Depends(get_db)):
    """Get all deliverable emails from database"""
    try:
        deliverable_emails = db.query(EmailValidation).filter(
            EmailValidation.deliverable == True
        ).all()
        
        # Convert to response format
        result = []
        for email in deliverable_emails:
            result.append({
                'id': email.id,
                'email': email.email,
                'format_valid': email.format_valid,
                'mx_valid': email.mx_valid,
                'deliverable': email.deliverable,
                'message': email.message,
                'validated_at': email.validated_at,
                'batch_id': email.batch_id or ""
            })
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.get("/download-deliverable-csv")
async def download_deliverable_csv(db: Session = Depends(get_db)):
    """Download deliverable emails as CSV"""
    try:
        deliverable_emails = db.query(EmailValidation).filter(
            EmailValidation.deliverable == True
        ).all()
        
        # Convert to DataFrame
        data = []
        for email in deliverable_emails:
            data.append({
                'id': email.id,
                'email': email.email,
                'format_valid': email.format_valid,
                'mx_valid': email.mx_valid,
                'deliverable': email.deliverable,
                'message': email.message,
                'validated_at': email.validated_at.isoformat() if email.validated_at else None,
                'batch_id': email.batch_id or ""
            })
        
        df = pd.DataFrame(data)
        
        # Create CSV in memory
        output = io.StringIO()
        df.to_csv(output, index=False)
        output.seek(0)
        
        # Return as streaming response
        response = StreamingResponse(
            io.BytesIO(output.getvalue().encode()),
            media_type="text/csv"
        )
        response.headers["Content-Disposition"] = f"attachment; filename=deliverable_emails_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"CSV generation error: {str(e)}")

@app.post("/batch-validate", response_model=BatchValidationResponse)
async def batch_validate(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Batch validate emails from CSV file
    
    CSV should have a column named 'email' with email addresses
    """
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a CSV")
    
    try:
        # Read CSV file
        contents = await file.read()
        df = pd.read_csv(io.StringIO(contents.decode('utf-8')))
        
        if 'email' not in df.columns:
            raise HTTPException(status_code=400, detail="CSV must have an 'email' column")
        
        # Generate batch ID
        batch_id = str(uuid.uuid4())
        
        results = []
        deliverable_count = 0
        
        for index, row in df.iterrows():
            email = row['email']
            if pd.isna(email) or not email:
                continue
                
            try:
                result = validator.comprehensive_validate(str(email))
                
                # Save to database with batch_id
                db_validation = EmailValidation(
                    email=result['email'],
                    format_valid=result['format_valid'],
                    mx_valid=result['mx_valid'],
                    deliverable=result['deliverable'],
                    message=result['message'],
                    validated_at=datetime.utcnow(),
                    batch_id=batch_id
                )
                db.add(db_validation)
                
                if result['deliverable']:
                    deliverable_count += 1
                
                result['validated_at'] = db_validation.validated_at
                results.append(EmailValidationResponse(**result))
                
            except Exception as e:
                # Log error but continue with other emails
                error_result = {
                    'email': str(email),
                    'format_valid': False,
                    'mx_valid': False,
                    'deliverable': False,
                    'message': f"Validation error: {str(e)}",
                    'validated_at': datetime.utcnow()
                }
                results.append(EmailValidationResponse(**error_result))
        
        db.commit()
        
        return BatchValidationResponse(
            batch_id=batch_id,
            total_emails=len(results),
            deliverable_count=deliverable_count,
            non_deliverable_count=len(results) - deliverable_count,
            results=results
        )
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Batch validation error: {str(e)}")

@app.get("/download-batch-deliverable/{batch_id}")
async def download_batch_deliverable(batch_id: str, db: Session = Depends(get_db)):
    """Download deliverable emails from a specific batch as CSV"""
    try:
        deliverable_emails = db.query(EmailValidation).filter(
            EmailValidation.batch_id == batch_id,
            EmailValidation.deliverable == True
        ).all()
        
        if not deliverable_emails:
            raise HTTPException(status_code=404, detail="No deliverable emails found for this batch")
        
        # Convert to DataFrame
        data = []
        for email in deliverable_emails:
            data.append({
                'id': email.id,
                'email': email.email,
                'format_valid': email.format_valid,
                'mx_valid': email.mx_valid,
                'deliverable': email.deliverable,
                'message': email.message,
                'validated_at': email.validated_at.isoformat() if email.validated_at else None,
                'batch_id': email.batch_id or ""
            })
        
        df = pd.DataFrame(data)
        
        # Create CSV in memory
        output = io.StringIO()
        df.to_csv(output, index=False)
        output.seek(0)
        
        # Return as streaming response
        response = StreamingResponse(
            io.BytesIO(output.getvalue().encode()),
            media_type="text/csv"
        )
        response.headers["Content-Disposition"] = f"attachment; filename=batch_{batch_id}_deliverable_emails.csv"
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"CSV generation error: {str(e)}")

@app.get("/batches")
async def get_batches(db: Session = Depends(get_db)):
    """Get all batch IDs with summary statistics"""
    try:
        batches = db.query(EmailValidation.batch_id).filter(
            EmailValidation.batch_id.isnot(None)
        ).distinct().all()
        
        batch_summaries = []
        for (batch_id,) in batches:
            if batch_id:  # Additional check for None
                batch_emails = db.query(EmailValidation).filter(
                    EmailValidation.batch_id == batch_id
                ).all()
                
                total = len(batch_emails)
                deliverable = sum(1 for email in batch_emails if email.deliverable)
                
                batch_summaries.append({
                    'batch_id': batch_id,
                    'total_emails': total,
                    'deliverable_count': deliverable,
                    'non_deliverable_count': total - deliverable,
                    'created_at': min(email.validated_at for email in batch_emails).isoformat()
                })
        
        return batch_summaries
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

# Usage example (for testing)
def test_emails():
    """Test function for development"""
    validator = EmailValidator()
    
    # Test emails
    test_emails = [
        'udyan.upal@northsouth.edu',
        'udyan.upall@gmail.com'
    ]
    
    #     'invalid@nonexistentdomain12345.com',
    # 'invalid-format@',
    # 'test@gmail.com'
    for email in test_emails:
        print(f"\nValidating: {email}")
        result = validator.comprehensive_validate(email)
        print(f"Format Valid: {result['format_valid']}")
        print(f"MX Valid: {result['mx_valid']}")
        print(f"Deliverable: {result['deliverable']}")
        print(f"Message: {result['message']}")
        print("-" * 50)

if __name__ == "__main__":
    # Run FastAPI server
    uvicorn.run(app, host="0.0.0.0", port=8000)