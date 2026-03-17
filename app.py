from flask import Flask, request, jsonify
import requests
from datetime import datetime
import json
import uuid
import os

app = Flask(__name__)

# ============================================
# PAYHERO CONFIGURATION - FILL THESE IN!
# ============================================
PAYHERO_USERNAME = "loBwimc9q2RJVtylnkh3"      # From dashboard
PAYHERO_PASSWORD = "5V3Fv3z7KsFwAFW0XTpp5xpaiYaGz8PyvzUaQ0hG"      # From dashboard  
PAYHERO_CHANNEL_ID = "5885"      # From Payment Channels (for Till 5247218)
YOUR_TILL_NUMBER = "5247218"                # Your actual Till number
# ============================================

# PayHero API endpoints - CORRECTED based on PayHero PHP package [citation:1]
PAYHERO_BASE_URL = "https://api.payhero.co.ke/api/v1"
PAYHERO_STK_PUSH_URL = f"{PAYHERO_BASE_URL}/stkpush"
PAYHERO_TRANSACTION_STATUS_URL = f"{PAYHERO_BASE_URL}/transaction/status"  # Note: different endpoint

# Store transactions (in production, use a database)
transactions = {}

@app.route('/')
def home():
    return 'Smart Cart Payment Server with PayHero (Till 5247218) is Running!'

@app.route('/initiate', methods=['POST'])
def initiate_payment():
    """
    Endpoint for NodeMCU to start a payment
    Forwards to PayHero API
    """
    try:
        # Get data from NodeMCU
        data = request.json
        print(f"Received from NodeMCU: {data}")
        
        # Extract data
        phone = data.get('phone')
        amount = data.get('amount')
        
        # Validate phone number
        if not phone or len(phone) < 10:
            return jsonify({'success': False, 'error': 'Invalid phone number'}), 400
        
        # Validate amount
        if not amount or amount <= 0:
            return jsonify({'success': False, 'error': 'Invalid amount'}), 400
        
        # Generate unique reference for this transaction
        external_reference = str(uuid.uuid4())
        
        # Your callback URL (your Render server)
        callback_url = "https://smart-cart-callback-server.onrender.com/payhero-callback"
        
        # Prepare PayHero API request
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        # PayHero expects phone in 254 format without +
        clean_phone = phone.replace('+', '').replace(' ', '')
        
        # Ensure phone starts with 254
        if clean_phone.startswith('0'):
            clean_phone = '254' + clean_phone[1:]
        elif clean_phone.startswith('7'):
            clean_phone = '254' + clean_phone
        
        # Based on PayHero PHP package documentation [citation:1]
        payload = {
            'amount': int(amount),
            'phone': clean_phone,
            'channel_id': PAYHERO_CHANNEL_ID,
            'external_reference': external_reference,
            'callback_url': callback_url,
            'description': f'Smart Cart Payment to Till {YOUR_TILL_NUMBER}'
        }
        
        print(f"Sending to PayHero: {json.dumps(payload, indent=2)}")
        print(f"URL: {PAYHERO_STK_PUSH_URL}")
        
        # Call PayHero API with Basic Authentication
        response = requests.post(
            PAYHERO_STK_PUSH_URL,
            json=payload,
            auth=(PAYHERO_USERNAME, PAYHERO_PASSWORD),
            headers=headers,
            timeout=30
        )
        
        print(f"PayHero response status: {response.status_code}")
        print(f"PayHero response body: {response.text}")
        
        if response.status_code in [200, 201, 202]:
            try:
                result = response.json()
            except:
                result = {'raw_response': response.text}
            
            # Store transaction as PENDING
            transactions[external_reference] = {
                'status': 'PENDING',
                'phone': phone,
                'amount': amount,
                'till_number': YOUR_TILL_NUMBER,
                'created_at': datetime.now().isoformat(),
                'payhero_response': result
            }
            
            return jsonify({
                'success': True,
                'message': 'STK Push initiated - Check your phone',
                'reference': external_reference,
                'till': YOUR_TILL_NUMBER
            })
        else:
            error_msg = f'PayHero API error: {response.status_code}'
            try:
                error_detail = response.json()
                error_msg += f" - {error_detail.get('message', error_detail.get('error', 'Unknown error'))}"
            except:
                error_msg += f" - {response.text}"
            
            return jsonify({
                'success': False, 
                'error': error_msg
            }), 500
            
    except Exception as e:
        print(f"Error in initiate_payment: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/payhero-callback', methods=['POST'])
def payhero_callback():
    """
    PayHero sends payment results here
    """
    try:
        data = request.json
        print(f"Callback received from PayHero: {json.dumps(data, indent=2)}")
        
        # Extract transaction details
        external_reference = data.get('external_reference') or data.get('reference') or data.get('ExternalReference')
        status = data.get('status') or data.get('Status')
        mpesa_receipt = data.get('mpesa_receipt') or data.get('receipt_number') or data.get('MpesaReceiptNumber')
        transaction_id = data.get('transaction_id') or data.get('TransactionId')
        
        if external_reference and external_reference in transactions:
            # Map status to our format
            if status and 'SUCCESS' in status.upper():
                transactions[external_reference]['status'] = 'SUCCESS'
            elif status and 'FAIL' in status.upper():
                transactions[external_reference]['status'] = 'FAILED'
            else:
                transactions[external_reference]['status'] = status or 'PENDING'
                
            transactions[external_reference]['mpesa_receipt'] = mpesa_receipt
            transactions[external_reference]['transaction_id'] = transaction_id
            transactions[external_reference]['updated_at'] = datetime.now().isoformat()
            transactions[external_reference]['full_callback'] = data
            
            print(f"Updated transaction {external_reference} to {transactions[external_reference]['status']}")
            
            # If receipt number exists, it's definitely a success
            if mpesa_receipt:
                transactions[external_reference]['status'] = 'SUCCESS'
                print(f"Receipt {mpesa_receipt} received - marking as SUCCESS")
        
        # Always acknowledge receipt - PayHero expects this
        return jsonify({'ResultCode': 0, 'ResultDesc': 'Success'})
        
    except Exception as e:
        print(f"Callback error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'ResultCode': 1, 'ResultDesc': 'Error'}), 500

@app.route('/status', methods=['GET'])
def check_status():
    """
    NodeMCU polls this endpoint to check payment status
    """
    reference = request.args.get('reference')
    
    if not reference:
        return jsonify({'error': 'Reference required'}), 400
    
    transaction = transactions.get(reference)
    
    if not transaction:
        return jsonify({'status': 'NOT_FOUND'}), 404
    
    return jsonify({
        'status': transaction['status'],
        'amount': transaction.get('amount'),
        'mpesa_receipt': transaction.get('mpesa_receipt'),
        'till': transaction.get('till_number')
    })

@app.route('/transactions', methods=['GET'])
def list_transactions():
    """View all transactions (for debugging)"""
    return jsonify(transactions)

@app.route('/test-auth', methods=['GET'])
def test_auth():
    """Test if your PayHero credentials work using the correct endpoint"""
    try:
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        
        # Try to get service wallet balance - CORRECT endpoint from PayHero docs [citation:1]
        test_url = f"{PAYHERO_BASE_URL}/wallet/service/balance"
        
        print(f"Testing auth with Username: {PAYHERO_USERNAME}")
        print(f"Testing URL: {test_url}")
        
        response = requests.get(
            test_url,
            auth=(PAYHERO_USERNAME, PAYHERO_PASSWORD),
            headers=headers,
            timeout=10
        )
        
        print(f"Test auth response: {response.status_code}")
        print(f"Response body: {response.text}")
        
        return jsonify({
            'status_code': response.status_code,
            'response': response.text[:200] + '...' if len(response.text) > 200 else response.text,
            'auth_working': response.status_code == 200,
            'url_tested': test_url
        })
    except Exception as e:
        print(f"Test auth error: {e}")
        return jsonify({'error': str(e), 'auth_working': False})

@app.route('/test-transaction-status', methods=['GET'])
def test_transaction_status():
    """Test the transaction status endpoint"""
    reference = request.args.get('reference', 'test-ref-123')
    
    try:
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        
        # Test transaction status endpoint
        test_url = f"{PAYHERO_BASE_URL}/transaction/status/{reference}"
        
        print(f"Testing transaction status URL: {test_url}")
        
        response = requests.get(
            test_url,
            auth=(PAYHERO_USERNAME, PAYHERO_PASSWORD),
            headers=headers,
            timeout=10
        )
        
        return jsonify({
            'status_code': response.status_code,
            'response': response.text[:200] + '...' if len(response.text) > 200 else response.text,
            'url_tested': test_url
        })
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/test-pay', methods=['POST'])
def test_payment():
    """Test endpoint to simulate a payment"""
    try:
        data = request.json
        phone = data.get('phone', '254712345678')
        amount = data.get('amount', 10)
        
        # Create a test transaction
        test_ref = "TEST" + str(uuid.uuid4())[:8]
        
        transactions[test_ref] = {
            'status': 'PENDING',
            'phone': phone,
            'amount': amount,
            'till_number': YOUR_TILL_NUMBER,
            'created_at': datetime.now().isoformat(),
            'test_mode': True
        }
        
        return jsonify({
            'success': True,
            'message': 'Test payment initiated',
            'reference': test_ref,
            'note': f'Use /status?reference={test_ref} to check status'
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    print("="*60)
    print("SMART CART PAYMENT SERVER WITH PAYHERO")
    print("="*60)
    print(f"Till Number: {YOUR_TILL_NUMBER}")
    print(f"PayHero Username: {PAYHERO_USERNAME}")
    print(f"PayHero Channel ID: {PAYHERO_CHANNEL_ID}")
    print("-"*60)
    print(f"Server running on Render: https://smart-cart-callback-server.onrender.com")
    print(f"Callback URL configured: https://smart-cart-callback-server.onrender.com/payhero-callback")
    print("-"*60)
    print("Try these endpoints:")
    print("  - GET  /                        - Home page")
    print("  - GET  /test-auth                - Test credentials (UPDATED URL)")
    print("  - GET  /test-transaction-status  - Test status endpoint")
    print("  - POST /test-pay                 - Test payment (no real money)")
    print("  - POST /initiate                  - Real payment (requires NodeMCU)")
    print("  - GET  /status?reference=XXX     - Check payment status")
    print("  - GET  /transactions              - View all transactions")
    print("-"*60)
    print("Press Ctrl+C to stop")
    print("="*60)
    
    # For Render deployment, use the PORT environment variable
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)