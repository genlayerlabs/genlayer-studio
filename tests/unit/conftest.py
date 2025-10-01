"""
Global fixtures and configuration for unit tests
"""
import os
import pytest
from unittest.mock import Mock, MagicMock, patch, AsyncMock
import json

# Check if we should use mocks
USE_MOCKS = os.getenv("TEST_WITH_MOCK_LLMS", "true").lower() == "true"


@pytest.fixture(autouse=True)
def mock_llm_providers():
    """
    Mock LLM provider calls specifically
    """
    if not USE_MOCKS:
        yield
        return
    
    patches = []
    
    # Mock OpenAI responses
    try:
        import openai
        
        # Mock chat completion response
        mock_completion = Mock()
        mock_completion.choices = [
            Mock(
                message=Mock(content="Mocked LLM response"),
                finish_reason="stop"
            )
        ]
        mock_completion.usage = Mock(total_tokens=100)
        
        mock_create = Mock(return_value=mock_completion)
        patches.append(patch('openai.ChatCompletion.create', mock_create))
    except ImportError:
        pass
    
    # Mock Anthropic responses
    try:
        import anthropic
        
        mock_response = Mock()
        mock_response.content = [Mock(text="Mocked Anthropic response")]
        mock_response.usage = Mock(input_tokens=50, output_tokens=50)
        
        mock_client = Mock()
        mock_client.messages.create = Mock(return_value=mock_response)
        patches.append(patch('anthropic.Anthropic', Mock(return_value=mock_client)))
    except ImportError:
        pass
    
    # Start all patches
    for p in patches:
        p.start()
    
    yield
    
    # Stop all patches
    for p in patches:
        p.stop()


@pytest.fixture(autouse=True)
def mock_external_services():
    """
    Automatically mock all external services when TEST_WITH_MOCK_LLMS=true
    Returns mock responses instead of throwing exceptions
    """
    if not USE_MOCKS:
        # If mocks are disabled, don't patch anything
        yield
        return
    
    patches = []
    
    # Mock web requests with realistic responses
    try:
        import requests
        
        # Create mock response object
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.ok = True
        mock_response.text = '{"success": true, "data": "mocked response"}'
        mock_response.json.return_value = {"success": True, "data": "mocked response"}
        mock_response.headers = {"content-type": "application/json"}
        mock_response.raise_for_status = Mock()
        
        # Mock request methods to return the mock response
        mock_get = Mock(return_value=mock_response)
        mock_post = Mock(return_value=mock_response)
        mock_put = Mock(return_value=mock_response)
        mock_delete = Mock(return_value=mock_response)
        
        patches.extend([
            patch('requests.get', mock_get),
            patch('requests.post', mock_post),
            patch('requests.put', mock_put),
            patch('requests.delete', mock_delete),
            patch('requests.Session.get', mock_get),
            patch('requests.Session.post', mock_post),
        ])
    except ImportError:
        pass
    
    # Mock urllib with realistic responses
    try:
        import urllib.request
        
        mock_response = Mock()
        mock_response.read.return_value = b'{"success": true, "data": "mocked response"}'
        mock_response.getcode.return_value = 200
        mock_response.headers = {"content-type": "application/json"}
        
        mock_urlopen = Mock(return_value=mock_response)
        patches.append(patch('urllib.request.urlopen', mock_urlopen))
    except ImportError:
        pass
    
    # Mock aiohttp with async responses
    try:
        import aiohttp
        
        # Create async mock response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.text = AsyncMock(return_value='{"success": true, "data": "mocked response"}')
        mock_response.json = AsyncMock(return_value={"success": True, "data": "mocked response"})
        mock_response.headers = {"content-type": "application/json"}
        
        # Create async session mock
        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None
        mock_session.get.return_value.__aenter__.return_value = mock_response
        mock_session.post.return_value.__aenter__.return_value = mock_response
        
        # Mock the ClientSession class
        mock_client_session = Mock(return_value=mock_session)
        patches.append(patch('aiohttp.ClientSession', mock_client_session))
    except ImportError:
        pass
    
    # Start all patches
    for p in patches:
        p.start()
    
    yield
    
    # Stop all patches
    for p in patches:
        p.stop()


@pytest.fixture(autouse=True)
def mock_webdriver():
    """
    Mock WebDriver/Selenium to prevent real browser instances
    Returns mock driver instances instead of throwing exceptions
    """
    if not USE_MOCKS:
        yield
        return
    
    patches = []
    
    # Mock selenium webdriver if present
    try:
        from selenium import webdriver
        
        # Create mock driver with common methods
        mock_driver = Mock()
        mock_driver.get = Mock()
        mock_driver.quit = Mock()
        mock_driver.close = Mock()
        mock_driver.find_element = Mock()
        mock_driver.find_elements = Mock()
        mock_driver.execute_script = Mock(return_value={"success": True})
        mock_driver.page_source = "<html><body>Mocked page</body></html>"
        mock_driver.title = "Mocked Page"
        mock_driver.current_url = "http://mocked.url"
        
        # Mock element for find_element operations
        mock_element = Mock()
        mock_element.text = "Mocked element text"
        mock_element.get_attribute = Mock(return_value="mocked attribute")
        mock_element.click = Mock()
        mock_element.send_keys = Mock()
        mock_element.is_displayed = Mock(return_value=True)
        
        mock_driver.find_element.return_value = mock_element
        mock_driver.find_elements.return_value = [mock_element]
        
        # Create mock constructors
        mock_chrome = Mock(return_value=mock_driver)
        mock_firefox = Mock(return_value=mock_driver)
        
        patches.extend([
            patch.object(webdriver, 'Chrome', mock_chrome),
            patch.object(webdriver, 'Firefox', mock_firefox),
        ])
    except ImportError:
        pass
    
    # Start patches
    for p in patches:
        p.start()
    
    yield
    
    # Stop patches
    for p in patches:
        p.stop()