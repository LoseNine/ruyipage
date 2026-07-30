# -*- coding: utf-8 -*-
"""BiDi network 模块命令"""


def add_intercept(driver, phases, url_patterns=None, contexts=None):
    """注册网络拦截

    Args:
        phases: 拦截阶段列表 ['beforeRequestSent', 'responseStarted', 'authRequired']
        url_patterns: URL 匹配模式列表
        contexts: 限定 context 列表

    Returns:
        {'intercept': str}  拦截 ID
    """
    params = {'phases': phases}
    if url_patterns:
        params['urlPatterns'] = url_patterns
    if contexts:
        params['contexts'] = contexts if isinstance(contexts, list) else [contexts]
    return driver.run('network.addIntercept', params)


def remove_intercept(driver, intercept_id):
    """移除拦截"""
    return driver.run('network.removeIntercept', {'intercept': intercept_id})


def continue_request(driver, request_id, body=None, cookies=None,
                     headers=None, method=None, url=None):
    """继续被拦截的请求（可修改）"""
    params = {'request': request_id}
    if body is not None:
        params['body'] = body
    if cookies is not None:
        params['cookies'] = cookies
    if headers is not None:
        params['headers'] = headers
    if method is not None:
        params['method'] = method
    if url is not None:
        params['url'] = url
    return driver.run('network.continueRequest', params)


def continue_response(driver, request_id, cookies=None, credentials=None,
                      headers=None, reason_phrase=None, status_code=None):
    """继续被拦截的响应（可修改）"""
    params = {'request': request_id}
    if cookies is not None:
        params['cookies'] = cookies
    if credentials is not None:
        params['credentials'] = credentials
    if headers is not None:
        params['headers'] = headers
    if reason_phrase is not None:
        params['reasonPhrase'] = reason_phrase
    if status_code is not None:
        params['statusCode'] = status_code
    return driver.run('network.continueResponse', params)


def continue_with_auth(driver, request_id, action='default', credentials=None):
    """处理 HTTP 认证

    Args:
        request_id: 请求 ID
        action: 'provideCredentials' / 'default' / 'cancel'
        credentials: {'type': 'password', 'username': str, 'password': str}
    """
    params = {'request': request_id, 'action': action}
    if credentials:
        params['credentials'] = credentials
    return driver.run('network.continueWithAuth', params)


def fail_request(driver, request_id):
    """中止被拦截的请求"""
    return driver.run('network.failRequest', {'request': request_id})


def provide_response(driver, request_id, body=None, cookies=None,
                     headers=None, reason_phrase=None, status_code=None):
    """为拦截的请求提供完整的模拟响应"""
    params = {'request': request_id}
    if body is not None:
        params['body'] = body
    if cookies is not None:
        params['cookies'] = cookies
    if headers is not None:
        params['headers'] = headers
    if reason_phrase is not None:
        params['reasonPhrase'] = reason_phrase
    if status_code is not None:
        params['statusCode'] = status_code
    return driver.run('network.provideResponse', params)


def set_cache_behavior(driver, behavior, contexts=None):
    """设置缓存行为（Firefox 私有扩展，非 W3C 标准）

    Args:
        behavior: 'default' / 'bypass'
        contexts: 限定 context 列表
    """
    params = {'cacheBehavior': behavior}
    if contexts:
        params['contexts'] = contexts if isinstance(contexts, list) else [contexts]
    return driver.run('network.setCacheBehavior', params)


def set_extra_headers(driver, headers, contexts=None, user_contexts=None):
    params = {"headers": headers}
    if contexts and user_contexts:
        raise ValueError("contexts and user_contexts cannot both be provided")
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    if user_contexts:
        params["userContexts"] = user_contexts if isinstance(user_contexts, list) else [user_contexts]
    return driver.run("network.setExtraHeaders", params)

def add_data_collector(driver, events=None, contexts=None, max_encoded_data_size=10485760, data_types=None, collector_type="blob", user_contexts=None):
    params = {
        "dataTypes": data_types if data_types else ["request", "response"],
        "maxEncodedDataSize": max_encoded_data_size,
        "collectorType": collector_type,
    }
    if contexts and user_contexts:
        raise ValueError("contexts and user_contexts cannot both be provided")
    if contexts:
        params["contexts"] = contexts if isinstance(contexts, list) else [contexts]
    if user_contexts:
        params["userContexts"] = user_contexts if isinstance(user_contexts, list) else [user_contexts]
    return driver.run("network.addDataCollector", params)

def remove_data_collector(driver, collector_id):
    """移除数据收集器"""
    return driver.run('network.removeDataCollector', {'collector': collector_id})


def get_data(driver, collector_id, request_id, data_type="response", disown=False):
    params = {"request": request_id, "dataType": data_type}
    if collector_id is not None:
        params["collector"] = collector_id
    if disown:
        params["disown"] = True
    return driver.run("network.getData", params)

def disown_data(driver, collector_id, request_id, data_type='response'):
    """释放收集器持有的数据（释放内存）"""
    return driver.run('network.disownData', {
        'collector': collector_id,
        'request': request_id,
        'dataType': data_type,
    })
