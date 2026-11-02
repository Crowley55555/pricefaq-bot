/**
 * AI-консультант по прайсу и FAQ — виджет (демо-витрина).
 *
 * Самодостаточный скрипт: инжектит все DOM-элементы и CSS,
 * отправляет сообщения на /api/chat, обрабатывает ответы.
 *
 * ── ИНСТРУКЦИЯ ПО ВСТАВКЕ ──
 * Добавьте перед </body> на любой HTML-странице:
 *
 *   <link rel="stylesheet" href="https://YOUR_SERVER/static/widget.css">
 *   <script src="https://YOUR_SERVER/static/widget.js"
 *           data-api="https://YOUR_SERVER"></script>
 *
 * Атрибут data-api указывает базовый URL бэкенда (без /api/chat).
 * Если атрибут не задан, используется тот же origin, что и у скрипта.
 */

(function () {
    "use strict";

    /* ─── Конфигурация ──────────────────────────────────────────────── */

    var scriptTag = document.currentScript;
    var API_BASE = (scriptTag && scriptTag.getAttribute("data-api")) || "";
    var API_URL = API_BASE + "/api/chat";

    var SESSION_KEY = "demo_chat_session_id";

    function getSessionId() {
        var id = localStorage.getItem(SESSION_KEY);
        if (!id) {
            id = "web_" + Math.random().toString(36).slice(2) + Date.now().toString(36);
            localStorage.setItem(SESSION_KEY, id);
        }
        return id;
    }

    /* ─── DOM-генерация ─────────────────────────────────────────────── */

    function createWidget() {
        // Плавающая кнопка
        var btn = document.createElement("button");
        btn.className = "shop-chat-btn";
        btn.setAttribute("aria-label", "Открыть чат");
        btn.innerHTML =
            '<svg viewBox="0 0 24 24"><path d="M20 2H4c-1.1 0-2 .9-2 2v18l4-4h14c1.1' +
            ' 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H5.17L4 17.17V4h16v12z"/></svg>';
        document.body.appendChild(btn);

        // Окно чата
        var win = document.createElement("div");
        win.className = "shop-chat-window";
        win.innerHTML =
            '<div class="shop-chat-header">' +
            '  <div class="shop-chat-header__avatar">🤖</div>' +
            '  <div class="shop-chat-header__info">' +
            '    <div class="shop-chat-header__title">AI-консультант</div>' +
            '    <div class="shop-chat-header__subtitle">Прайс и FAQ (демо)</div>' +
            "  </div>" +
            '  <button class="shop-chat-header__close" aria-label="Закрыть">&times;</button>' +
            "</div>" +
            '<div class="shop-chat-messages" id="shop-messages"></div>' +
            '<div class="shop-buttons" id="shop-buttons"></div>' +
            '<div class="shop-chat-input">' +
            '  <input class="shop-chat-input__field" id="shop-input" placeholder="Напишите сообщение…" />' +
            '  <button class="shop-chat-input__send" id="shop-send" aria-label="Отправить">' +
            '    <svg viewBox="0 0 24 24"><path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/></svg>' +
            "  </button>" +
            "</div>";
        document.body.appendChild(win);

        var messagesEl = document.getElementById("shop-messages");
        var buttonsEl = document.getElementById("shop-buttons");
        var inputEl = document.getElementById("shop-input");
        var sendBtn = document.getElementById("shop-send");
        var closeBtn = win.querySelector(".shop-chat-header__close");

        var isOpen = false;

        function toggle() {
            isOpen = !isOpen;
            win.classList.toggle("shop-chat-window--open", isOpen);
            if (isOpen && messagesEl.children.length === 0) {
                addBotMessage("Здравствуйте! Я — AI-консультант (демо-витрина). Отвечу по услугам и помогу записаться.\n\nНажмите «Старт» или задайте вопрос.");
                addButtons([
                    { label: "Старт", value: "Старт" },
                ]);
            }
            if (isOpen) inputEl.focus();
        }

        btn.addEventListener("click", toggle);
        closeBtn.addEventListener("click", toggle);

        function addMessage(text, cls) {
            var div = document.createElement("div");
            div.className = "shop-msg " + cls;
            div.textContent = text;
            messagesEl.appendChild(div);
            messagesEl.scrollTop = messagesEl.scrollHeight;
            return div;
        }

        function addBotMessage(text) { return addMessage(text, "shop-msg--bot"); }
        function addUserMessage(text) { return addMessage(text, "shop-msg--user"); }
        function addTyping() { return addMessage("Печатает…", "shop-msg--typing"); }
        function removeTyping(el) {
            if (el && el.parentNode) el.parentNode.removeChild(el);
        }

        function addServiceCard(data) {
            var card = document.createElement("div");
            card.className = "shop-service-card";
            var html = "";
            if (data.service) html += '<div class="shop-service-card__name">' + escHtml(data.service) + "</div>";
            if (data.code) html += '<div class="shop-service-card__detail">Код: ' + escHtml(data.code) + "</div>";
            if (data.category) html += '<div class="shop-service-card__detail">Категория: ' + escHtml(data.category) + "</div>";
            if (data.price_from != null) html += '<div class="shop-service-card__price">от ' + escHtml(String(data.price_from)) + " ₽</div>";
            if (data.duration_min != null) html += '<div class="shop-service-card__detail">~ ' + escHtml(String(data.duration_min)) + " мин.</div>";
            card.innerHTML = html;
            messagesEl.appendChild(card);
            messagesEl.scrollTop = messagesEl.scrollHeight;
        }

        function addButtons(buttons) {
            buttonsEl.innerHTML = "";
            if (!buttons || !buttons.length) return;
            buttons.forEach(function (b) {
                var btnEl = document.createElement("button");
                btnEl.className = "shop-buttons__btn";
                btnEl.textContent = b.label;
                btnEl.addEventListener("click", function () {
                    sendMessage(b.value);
                });
                buttonsEl.appendChild(btnEl);
            });
        }

        function clearButtons() {
            buttonsEl.innerHTML = "";
        }

        var sending = false;

        async function sendMessage(text) {
            if (sending || !text.trim()) return;
            sending = true;
            sendBtn.disabled = true;
            clearButtons();

            addUserMessage(text);
            inputEl.value = "";

            var typingEl = addTyping();

            try {
                var resp = await fetch(API_URL, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        message: text,
                        session_id: getSessionId(),
                        source: "web",
                    }),
                });

                var data = await resp.json();
                removeTyping(typingEl);

                if (data.reply) addBotMessage(data.reply);

                if (data.action === "show_service" && data.service_data) {
                    addServiceCard(data.service_data);
                }

                if (data.buttons && data.buttons.length) {
                    addButtons(data.buttons);
                }
            } catch (err) {
                removeTyping(typingEl);
                addBotMessage("Не удалось связаться с сервером. Попробуйте позже.");
                console.error("Chat widget error:", err);
            }

            sending = false;
            sendBtn.disabled = false;
        }

        sendBtn.addEventListener("click", function () {
            sendMessage(inputEl.value);
        });
        inputEl.addEventListener("keydown", function (e) {
            if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendMessage(inputEl.value);
            }
        });
    }

    /* ─── Утилиты ───────────────────────────────────────────────────── */

    function escHtml(s) {
        var d = document.createElement("div");
        d.appendChild(document.createTextNode(s));
        return d.innerHTML;
    }

    /* ─── Инициализация ─────────────────────────────────────────────── */

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", createWidget);
    } else {
        createWidget();
    }
})();
